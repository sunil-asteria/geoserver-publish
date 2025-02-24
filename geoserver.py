import argparse
import json
import logging
import os
from base64 import b64encode
from pathlib import Path

import requests
from azure.storage.blob import BlobClient, BlobServiceClient, ContainerClient
from requests.auth import HTTPBasicAuth

logging.basicConfig(level=logging.INFO)

# Global variables
GEOSERVER_URL = None
USER = None
PASSWORD = None
CONTAINER_NAME = None
DIRECTORY = None
CONNECTION_STRING = None
SAS = None
PUBLISH = None
DELETE = None
STORAGE_URL = "https://gailwmssa.blob.core.windows.net"


def basic_auth():
    token = b64encode(f"{USER}:{PASSWORD}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


# Send HTTP request to GeoServer URL
def send_request(method, url, payload):
    headers = {"Content-Type": "application/json", "Authorization": basic_auth()}
    response = requests.request(method, url, headers=headers, data=payload)

    return response.status_code, response.text


# Return a list of geotiff blob URLs, within the given directory
def list_blobs(sub_directory):
    blob_service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    blob_list = container_client.list_blobs(name_starts_with=sub_directory)
    blob_url_list = []
    for blob in blob_list:
        blob_name = (
            f"{STORAGE_URL}/{CONTAINER_NAME}/{blob.name}"
        )
        if blob_name.endswith(".tif"):
            blob_url_list.append(blob_name)

    return blob_url_list


# Send GET request to GeoServer and get the status of a workspace
def get_workspace(workspace_name):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}"
    status_code, response_text = send_request("GET", url, {})

    return status_code, response_text

def get_layers(workspace_name):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/layers.json"
    status_code, response_text = send_request("GET", url, {})

    return status_code, response_text

def get_layer_group(workspace_name, layer_group_name):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/layergroups/{layer_group_name}.json"
    status_code, response_text = send_request("GET", url, {})

    return status_code, response_text

def get_stores(workspace_name):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/coveragestores.json"
    status_code, response_text = send_request("GET", url, {})

    return status_code, response_text

# Create a workspace
def create_workspace(workspace_name):
    # Find out if the workspace already exists or need to be created
    status_code, response_text = get_workspace(workspace_name)
    if status_code == 200:
        logging.info(
            f"Workspace: {workspace_name} already exists. Response: {response_text}. Status: {status_code}"
        )
    else:
        url = f"{GEOSERVER_URL}/workspaces"
        payload = json.dumps({"workspace": {"name": workspace_name}})

        status_code, response_text = send_request("POST", url, payload)
        logging.info(f"Workspace: {workspace_name} created. Response: {response_text}. Status: {status_code}")


# Create a GeoTiff store. 
# Convention --> {Section Name}_{Image Name}. eg: 10260001_S_Name_K.Test_Orthomosaic_19_COG.tif,
# Where Section Name = 10260001_S_Name & Image Name = K.Test_Orthomosaic_19_COG.tif
def create_coveragestore(workspace_name, blob_url):
    # Store name cannot have special characters, but can have spaces. Whereas URL cannot have spaces, hence it needs to be encoded.
    image_name = Path(blob_url).stem
    split_names = blob_url.split("/")
    store_name = f"{split_names[7]}_{image_name}"
    #store_name = requests.utils.unquote(store_name)
    
    # Encode the URL
    encoded_blob_url = requests.utils.requote_uri(blob_url)
    encoded_blob_url = f"cog://{encoded_blob_url}?{SAS}"

    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/coveragestores?configure=all"
    payload = json.dumps(
        {
            "coverageStore": {
                "workspace": workspace_name,
                "name": store_name,
                "enabled": True,
                "metadata": [
                    {
                        "entry": {
                            "@key": "CogSettings.Key",
                            "cogSettings": {
                                "useCachingStream": False,
                                "rangeReaderSettings": "HTTP",
                            },
                        }
                    }
                ],
                "type": "GeoTIFF",
                "url": encoded_blob_url,
            }
        }
    )

    status_code, response_text = send_request("POST", url, payload)
    logging.info(f"Coverage store: {response_text}. Status: {status_code}")

    if status_code not in [200,201]:
        store_name = None

    return store_name


# Create a layer under the given workspace. Convention --> Same name as store name
def create_layer(workspace_name, store_name, blob_url):
    image_name = Path(blob_url).stem
    image_name = os.path.basename(image_name).split(".")[0]

    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/coveragestores/{store_name}/coverages"
    payload = json.dumps({
        "coverage": {
            "name": store_name,
            "nativeName": store_name,
            "title": image_name,
            "nativeCoverageName": image_name,
            "nativeFormat": "GeoTIFF"
        }
    })

    status_code, response_text = send_request("POST", url, payload)
    logging.info(f"Layer: {response_text}. Status: {status_code}")


# Create new layer group/Update the existing layer group, with newly published layers.
# Convention --> C01_WorkspaceName_SectionCode. eg: C01_JHBDPL_10260001
def create_layer_group(workspace_name, new_published_layers, blob_url):
    logging.info(f"Layers to be added to the layer group: {new_published_layers}")

    new_published = []
    new_styles = []
    
    # Get all published layers in the workspace and add only the newly published layers to the layer group. Ignore the rest.
    status_code, layers = get_layers(workspace_name)
    layers = json.loads(layers)

    for layer in layers.get("layers").get("layer"):
        if layer["name"] in new_published_layers:
            new_published.append(
                {
                    "@type": "layer",
                    "name": f"{workspace_name}:{layer['name']}",
                    "href": layer["href"],
                }
            )
            new_styles.append("")  # Default style.

    # Create a layer group name
    split_names = blob_url.split("/")
    split_cycle_name = split_names[6].split("_")
    # Make it generic enough. If cycle name doesn't have a cycle number in it, use the whole cycle name. Else, use just the cycle number.
    if len(split_cycle_name) > 1:
        cycle_number = split_cycle_name[1]
    else:
        cycle_number = split_cycle_name
    section_code = split_names[7].split("_")[0]
    layer_group_name = f"C{cycle_number}_{split_names[5]}_{section_code}"

    # Check if the layer group name exists in the workspace
    status_code, response_text = get_layer_group(workspace_name, layer_group_name)

    # If layer group already exists in the workspace, then update the existing layer group
    if status_code == 200:
        logging.info(f"Layer group: {layer_group_name} already exists")
        method = "PUT"
        url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/layergroups/{layer_group_name}.json"
        payload = json.loads(response_text)
        published = payload.get("layerGroup").get("publishables").get("published")
        
        # For the default styles --> style is a string in case of single layer and list in case of multiple layers
        style = (payload.get("layerGroup").get("styles").get("style"))
        if isinstance(published, dict): # It is a dictionary when only single layer is present in the layergroup
            payload.get("layerGroup").get("publishables")["published"] = [published] + new_published
            payload.get("layerGroup").get("styles")["style"] = [style] + new_styles
        elif isinstance(published, list): # It is a list when multiple layers are present in the layergroup
            payload.get("layerGroup").get("publishables")["published"] = published + new_published
            payload.get("layerGroup").get("styles")["style"] = style + new_styles
        else:
            logging.error(f"ERROR: Not adding the layer to the layergroup: {layer_group_name}...........")
    # If layer group doesn't exist in the workspace, then create a new layer group
    else:
        logging.info(f"Layer group: {layer_group_name} creating...")
        method = "POST"
        url = GEOSERVER_URL + "/layergroups"
        payload = {
            "layerGroup": {
                "name": layer_group_name,
                "mode": "SINGLE",
                "title": layer_group_name,
                "workspace": {"name": workspace_name},
                "publishables": {"published": new_published},
            }
        }

    payload = json.dumps(payload)
    status_code, response_text = send_request(method, url, payload)
    logging.info(response_text)
    if status_code in [200, 201]:
        logging.info(
            f"Layer group: {layer_group_name} created/updated. Status: {status_code}"
        )
    else:
        logging.error(
            f"ERROR: Could not create/update the layer group {layer_group_name}. Status: {status_code}"
        )


# Publish all .tif files within the given sub directory
def publish_folder(sub_directory):
    workspace_name = sub_directory.split("/")[1]

    # Get a list of all Geotiffs, under the sub directory
    blob_url_list = list_blobs(sub_directory)

    # Create a workspace
    create_workspace(workspace_name)

    store_names = []
    # Iterate over each blob, create a GeoTiff coverage store and publish the layer
    for blob_url in blob_url_list:
        logging.info("\n")
        store_name = create_coveragestore(workspace_name, blob_url)
        if store_name:
            store_names.append(store_name)
            create_layer(workspace_name, store_name, blob_url)
    
    if store_names:
        # Create/Update layer group and add the newly published layers to the group
        create_layer_group(workspace_name, store_names, blob_url_list[0])
    else:
        logging.info("Nothing to be added to layer group")

# Delete all published stores, layers and layer group, corresponding to the given input directory
def unpublish_folder(sub_directory):
    split_names = sub_directory.split("/")
    workspace_name = split_names[1]

    # Get all stores published under the workspace
    status_code, stores = get_stores(workspace_name)
    stores = json.loads(stores)

    # Get all stores of interest only (i.e. corresponding the the given input directory only)
    stores_to_delete = []

    # If stores not empty
    if not stores.get("coverageStores") == "":
        for store in stores.get("coverageStores").get("coverageStore"):
            store_name = store["name"]
            section_code = store_name.split("_")[0]
            if section_code in sub_directory:
                stores_to_delete.append(store_name)

        # Delete 1 store at a time
        for i, store_name in enumerate(stores_to_delete):
                logging.info(f"\nDeleting store {i+1}/{len(stores_to_delete)}: {store_name} ...")
                url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/coveragestores/{store_name}.json?recurse=true"
                status_code, response_text = send_request("DELETE", url, {})
                
                logging.info(response_text)
                if status_code in [200, 201]:
                    logging.info(f"Deleted store: {store_name}. Status: {status_code}")
                else:
                    logging.error(f"ERROR: Could not delete the store: {store_name}. Status: {status_code}")
    else:
        logging.info(f"Nothing to unpublish")


# Parse command line arguments and assign to global variables
def parse_args():
    global GEOSERVER_URL, USER, PASSWORD, CONTAINER_NAME, DIRECTORY, CONNECTION_STRING, SAS, PUBLISH, DELETE

    message = "Script to scan a directory and publish all tiff files, to GeoServer"
    parser = argparse.ArgumentParser(description=message)
    parser.add_argument(
        "-g",
        "--geoserver_url",
        help="GeoServer REST URL eg: http://localhost:8080/geoserver/rest",
        required=True,
    )
    parser.add_argument("-u", "--user", help="GeoServer login user name", required=True)
    parser.add_argument(
        "-p", "--password", help="GeoServer login password", required=True
    )
    parser.add_argument(
        "-c", "--container", help="Azure storage container name", required=True
    )
    parser.add_argument(
        "-d", "--directory", help="Directory containing GeoTiff files", required=True
    )
    parser.add_argument(
        "-cs",
        "--azure_connection_string",
        help="Azure storage connection string",
        required=True,
    )
    parser.add_argument("-sas", "--azure_sas", help="Azure storage SAS", required=True)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-P", "--publish", help="Publish images", action='store_true')
    group.add_argument("-D", "--delete", help="Delete published images", action='store_true')

    args = parser.parse_args()

    GEOSERVER_URL = args.geoserver_url
    USER = args.user
    PASSWORD = args.password
    CONTAINER_NAME = args.container
    DIRECTORY = args.directory
    CONNECTION_STRING = args.azure_connection_string
    SAS = args.azure_sas
    PUBLISH = args.publish
    DELETE = args.delete


if __name__ == "__main__":
    logging.info("Parsing the arguments")
    parse_args()

    if PUBLISH:
        logging.info(f"Publishing the directory: {DIRECTORY}")
        publish_folder(DIRECTORY)
    else:
        logging.info(f"Un-publishing the directory: {DIRECTORY}")
        unpublish_folder(DIRECTORY)
