# Publish a directory of tiff files to GeoServer

# Execution
The Python file `geoserver.py` is the code to execute and publish the directory of tiff files to remote GeoServer. 

**Package installation**: `pip install -r requirements.txt`

**Usage**: `python3 geoserver.py --help`.

**Usage**: 
```
python3 geoserver.py -g "http://localhost:8080/geoserver/rest" -u admin -p geoserver -c geo-data-clone -cs "azure_connection_string" -sas "azure_sas_token" -d "Group-D/VIZAG/Cycle_01/GKONDURU-IP2"
```

# Installing GeoServer
Build the `compose.yaml` and run

**Usage**: `docker compose up`

Alternately,
```
docker run -it -p8080:8080 --mount type=bind,src=/home/sunil/Downloads/geoserver_datadir,target=/opt/geoserver_data --env INSTALL_EXTENSIONS=true --env COMMUNITY_EXTENSIONS="cog-azure cog-http" docker.osgeo.org/geoserver:2.26.x
```

# Details
The python file `geoserver.py` automatically creates a workspace if not already present, otherwise uses the existing workspace for updation. A GeoTiff store is created and the corresponding layer is published in GeoServer, for each of the GeoTiff file present in the input directory. Ultimately, a layer group is created and each of the published layers are added to the layer group. If the layer group already exists, then then existing layer group simply gets appended with the newly published layers.

## Naming conventions:

Sample azure storage container (A parameter to the code): `geo-data-clone`

Sample input directory (i.e. the directory within the azure storage container where GeoTiff files are present. A parameter to the code): `Group-D/VIZAG/Cycle_01/GKONDURU-IP2`

**Following are created by the python code, when an image gets published in GeoServer**:
Sample GeoTiff blob which gets published: `Group-D/VIZAG/Cycle_01/GKONDURU-IP2/IPS G KONDURU-IP2 SURYAPET_Orthomosaic_35_COG.tif`

Workspace name: `VIZAG`

Store name: `Cycle_01_GKONDURU-IP2_IPS G KONDURU-IP2 SURYAPET_Orthomosaic_35_COG`

Layer name: `Cycle_01_GKONDURU-IP2_IPS G KONDURU-IP2 SURYAPET_Orthomosaic_35_COG`

Layer title: `IPS G KONDURU-IP2 SURYAPET_Orthomosaic_35_COG`

Layer group name: `Group-D_VIZAG_Cycle_01_GKONDURU-IP2`

# Alternate way of publishing, using imagemosaic
This approach is a faster way to publish and manage the images. However, this would need more work, as the performance of the rendered images is poor and would need an in-depth analysis. The source code `geoserver.py` does not include this option.

## Steps:
1. Mount the blob storage on the system running GeoServer
2. Run GeoServer by mounting the path inside the container
3. Create imagemosaic store by pointing to the local mounted directory

```
wget https://packages.microsoft.com/config/ubuntu/22.04/packages-microsoft-prod.deb
dpkg -i packages-microsoft-prod.deb
apt-get update
apt-get install libfuse3-dev fuse3 
apt-get install blobfuse2

mkdir cache
mkdir mnt_storage
#Give the cache directory path, inside config.yaml
blobfuse2 mount ./mnt_storage/ --config-file=./config.yaml

#Edit /etc/fuse.conf and uncomment user_allow_other
docker run -it -p8080:8080 -v /home/sunil/Downloads/azure_storage:/mnt/azure_storage --mount type=bind,src=/home/sunil/Downloads/geoserver_datadir,target=/opt/geoserver_data --env INSTALL_EXTENSIONS=true --env COMMUNITY_EXTENSIONS="cog-azure cog-http" docker.osgeo.org/geoserver:2.26.x
```





