import argparse
import json
import logging
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


def basic_auth():
    token = b64encode(f"{USER}:{PASSWORD}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


# Send HTTP request to GeoServer URL
def send_request(method, url, payload):
    headers = {"Content-Type": "application/json", "Authorization": basic_auth()}
    response = requests.request(method, url, headers=headers, data=payload)

    return response.status_code, response.text


# List all tif files withing a directory. Return workspace name and list of geotiff blob URLs to publish to GeoServer
def list_blobs(sub_directory):
    blob_service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    blob_list = container_client.list_blobs(name_starts_with=sub_directory)

    workspace_name = ""
    blob_url_list = []
    for blob in blob_list:
        split_names = blob.name.split("/")
        workspace_name = split_names[1]
        blob_name = (
            f"https://gailwmssa.blob.core.windows.net/{CONTAINER_NAME}/{blob.name}"
        )
        if blob_name.endswith(".tif"):
            blob_url_list.append(blob_name)

    return workspace_name, blob_url_list


# Send GET request to GeoServer and get the status of a workspace
def get_workspace(workspace_name):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}"
    status_code, response_text = send_request("GET", url, {})

    return status_code, response_text


# Create a workspace
def create_workspace(workspace_name):
    # Find out if the workspace already exists or need to be created
    status_code, response_text = get_workspace(workspace_name)
    if status_code == 200:
        logging.info(
            f"Workspace: {workspace_name} already exists. Response: {response_text}"
        )
    else:
        url = f"{GEOSERVER_URL}/workspaces"
        payload = json.dumps({"workspace": {"name": workspace_name}})

        status_code, response_text = send_request("POST", url, payload)
        logging.info(f"Workspace: {workspace_name} created. Response: {response_text}")


# Create a GeoTiff store (named as store_name), image URL (blob_url), under the workspace (workspace_name)
def create_coveragestore(workspace_name, store_name, blob_url):
    blob_url = f"cog://{blob_url}?{SAS}"

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
                "url": blob_url,
            }
        }
    )

    status_code, response_text = send_request("POST", url, payload)
    logging.info(f"Coverage store: {response_text}")


# Publish a layer under the given workspace
def create_layer(workspace_name, store_name, blob_url):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/coveragestores/{store_name}/coverages"
    blob_url = requests.utils.unquote(blob_url)
    split_names = blob_url.split("/")
    title = f"{split_names[4]}_{split_names[5]}_{split_names[6]}_{split_names[7]}"

    """
    Native name MUST be = image name or else, Geoserver throws error.
    Note 1: Store names, native name of a layer are both equal to the image name
    Note 2: Though native name and title are different, after publishing, Geoserver assigns image name to name & native name too. Perhaps, a bug ?
    """
    payload = json.dumps({"coverage": {"nativeName": store_name, "title": title}})

    status_code, response_text = send_request("POST", url, payload)
    logging.info(f"Layer: {response_text}")


def get_layers(workspace_name):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/layers.json"
    status_code, response_text = send_request("GET", url, {})

    return response_text


def get_layer_group(workspace_name, layer_group_name):
    url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/layergroups/{layer_group_name}.json"
    status_code, response_text = send_request("GET", url, {})

    return status_code, response_text


"""
Create new layer group/Update the existing layer group, with newly published layers.
Params:
workspace_name: Workspace name
new_published_layers: list of image names (which is same as store name)
blob_url: one of the newly published blob URL
"""


def create_layer_group(workspace_name, new_published_layers, blob_url):
    logging.info(f"Layers to be added to the layer group: {new_published_layers}")

    new_published = []
    new_styles = []
    # Get all published layers in the workspace and add only the newly published layers to the layer group. Ignore the rest.
    layers = json.loads(get_layers(workspace_name))
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
    layer_group_name = f"{split_names[4]}_{split_names[5]}_{split_names[6]}"

    # Check if the layer group name exists in the workspace
    status_code, response_text = get_layer_group(workspace_name, layer_group_name)

    # If layer group already exists in the workspace, then update the existing layer group
    if status_code == 200:
        logging.info(f"Layer group: {layer_group_name} already exists")
        method = "PUT"
        url = f"{GEOSERVER_URL}/workspaces/{workspace_name}/layergroups/{layer_group_name}.json"
        payload = json.loads(response_text)
        published = payload.get("layerGroup").get("publishables").get("published")
        style = (
            payload.get("layerGroup").get("styles").get("style")
        )  # For default styles, style is a string, in case of single layer and list in case of multiple layers
        if isinstance(
            published, dict
        ):  # It is a dictionary when only single layer is present in the layergroup
            payload.get("layerGroup").get("publishables")["published"] = [
                published
            ] + new_published
            payload.get("layerGroup").get("styles")["style"] = [style] + new_styles
        elif isinstance(
            published, list
        ):  # It is a list when multiple layers are present in the layergroup
            payload.get("layerGroup").get("publishables")["published"] = (
                published + new_published
            )
            logging.info(payload)
            logging.info(style)
            logging.info(type(style))
            payload.get("layerGroup").get("styles")["style"] = style + new_styles
            logging.info(payload)
        else:
            logging.error("ERROR: Not adding the layer to the layergroup ...........")
    # If layer group doesn't exist in the workspace, then create a new layer group
    else:
        logging.info("Layer group: {layer_group_name} creating...")
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

    logging.info("-----------")
    payload = json.dumps(payload)
    logging.info(f"method: {method}, {url}, payload: {payload}")
    status_code, response_text = send_request(method, url, payload)
    if status_code in [200, 201]:
        logging.info(
            f"Layer group: {layer_group_name} created/updated with the following layers {new_published_layers}"
        )
    else:
        logging.error(
            f"ERROR: Status: {status_code} could not create/update the layer group {layer_group_name} ..........."
        )
    logging.info(response_text)


# Publish all .tif files within the given sub directory
def publish_folder(sub_directory):
    # Get the workspace name and a list all blobs in the given sub directory (to publish in GeoServer)
    workspace_name, blob_url_list = list_blobs(sub_directory)

    # Create a workspace
    create_workspace(workspace_name)

    store_names = []
    for blob_url in blob_url_list:
        blob_url = requests.utils.requote_uri(blob_url)

        # Create a new Geotiff store with its name = image name
        # Store name cannot have special characters, but can have spaces. Whereas URL cannot have spaces, hence it needs to be encoded.
        store_name = Path(blob_url).stem
        store_name = requests.utils.unquote(store_name)
        store_names.append(store_name)
        create_coveragestore(workspace_name, store_name, blob_url)

        # Create and publish a layer.
        # Note: Store name, native name of a layer are both equal to the image name
        create_layer(workspace_name, store_name, blob_url)

    # Create/Update layer group and add the newly published layers to the group
    create_layer_group(workspace_name, store_names, blob_url_list[0])


# Parse command line arguments and assign to global variables
def parse_args():
    global GEOSERVER_URL, USER, PASSWORD, CONTAINER_NAME, DIRECTORY, CONNECTION_STRING, SAS

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

    args = parser.parse_args()

    GEOSERVER_URL = args.geoserver_url
    USER = args.user
    PASSWORD = args.password
    CONTAINER_NAME = args.container
    DIRECTORY = args.directory
    CONNECTION_STRING = args.azure_connection_string
    SAS = args.azure_sas


if __name__ == "__main__":
    parse_args()

    logging.info(f"GEOSERVER_URL: {GEOSERVER_URL}")
    logging.info(f"USER: {USER}")
    logging.info(f"PASSWORD: {PASSWORD}")
    logging.info(f"CONTAINER_NAME: {CONTAINER_NAME}")
    logging.info(f"DIRECTORY: {DIRECTORY}")
    logging.info(f"CONNECTION_STRING: {CONNECTION_STRING}")
    logging.info(f"SAS: {SAS}")

    publish_folder(DIRECTORY)
