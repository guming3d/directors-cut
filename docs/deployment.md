# Deployment (Azure Container Apps)

The backend and dashboard run as two Azure Container Apps that share one environment and pull images from Azure Container Registry.

## First deployment

1. Populate `.env` with your Azure subscription ID, target region, resource group, registry, and Container Apps environment and app names. Sample values are in `.env.example`.
2. Sign in with `az login`, then deploy both apps:
   ```bash
   ./deploy.sh
   ```

## Updates

```bash
./update.sh                          # rebuild and roll out both apps
./update.sh --component backend      # backend only
./update.sh --component frontend     # frontend only
./update.sh --image-tag <tag>        # override the image tag (default: current Git SHA, or a timestamp)
```

Deploying the backend ends any render in progress, so `update.sh` refuses to update it while cloud jobs are queued or running. Add `--force` to deploy anyway.

Docker Hub is not contacted at build time: the scripts import `python:3.11-slim` and `node:20-alpine` into your registry once with `az acr import`, then build from those copies. Set `DOCKER_BASE_REFRESH=1` to re-import them. Set `DEPLOY_LOG_LEVEL` to override `LOG_LEVEL` for the cloud backend only.

## Persistent storage

Locally, jobs and media live in `output/`. In Azure, `update.sh` and `deploy.sh` set `OUTPUT_STORAGE=blob`: the backend mirrors `output/` to a private blob container (storage account `<registry>out`, created with shared-key and public access disabled) through its managed identity, which gets "Storage Blob Data Contributor". On startup it restores the job database before loading jobs, then fetches videos and images on first use, so redeploys no longer erase history.

Because the subscription's policy disables public network access on storage accounts, the backend reaches the account through a private endpoint: `deploy.sh` creates a virtual network (`AZURE_VNET_NAME`, default `sora-workflow-vnet`, in `AZURE_LOCATION`) and runs the Container Apps environment inside it. An existing environment cannot be moved into a network, so switching an older deployment to blob storage needs a new `AZURE_CONTAINERAPPS_ENVIRONMENT` (and new app names, or delete the old apps first).

Set `DEPLOY_OUTPUT_STORAGE=local` to skip all of this and keep the old temporary-disk behaviour.

The backend runs as a single replica, because jobs are held in memory.
