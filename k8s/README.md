# Deploy the 2-container app to AKS (namespace: compliance-v2)

Runs **alongside** the existing single-container app in `default` (untouched).
The frontend is exposed on its **own public IP** (LoadBalancer Service) - separate
from the default app's `20.62.224.193`.

## 1. Build + push both images to ACR
```bash
az acr login -n acrcompliancedoc

docker build -t acrcompliancedoc.azurecr.io/compliance-backend:latest  ../backend-svc
docker push  acrcompliancedoc.azurecr.io/compliance-backend:latest

docker build -t acrcompliancedoc.azurecr.io/compliance-frontend:latest ../frontend-svc
docker push  acrcompliancedoc.azurecr.io/compliance-frontend:latest
```

## 2. Namespace + secret
```bash
kubectl apply -f namespace.yaml
kubectl create secret generic compliance-env \
  --from-file=settings.env=../backend-svc/config/settings.env \
  -n compliance-v2
```

## 3. Deploy (backend + frontend only - NO ingress)
```bash
kubectl apply -f backend.yaml
kubectl apply -f frontend.yaml
```
> The API server is IP-restricted. If `kubectl` times out, use
> `az aks command invoke -g cyberai -n compliance-aks --command "kubectl apply -f frontend.yaml" --file frontend.yaml`
> (one `command invoke` per file; apply named files, never `-f .`).

## 4. Get the new public IP + open the app
```bash
kubectl get svc frontend -n compliance-v2 -o wide
```
`EXTERNAL-IP` shows `<pending>` for ~1-2 min, then a **new IP**. Browse **http://<that-ip>**
(no host header needed). Sign in **admin / admin** on first run.

## 5. Whitelist the AKS EGRESS IP on Azure (or assessments fail)
The **inbound** LoadBalancer IP above is the app's front door. The pods make **outbound**
calls to Azure OpenAI / AI Search from the cluster's **egress** IP - whitelist THAT on
`cyberai-opus-resource` and `rag-cyberai` (same `az ...network-rule add` / `--ip-rules`
pattern you used before). Two different IPs, two different purposes.

## Notes
- **Own public IP:** `frontend.yaml` Service is `type: LoadBalancer` -> Azure assigns a new IP.
  For a **fixed** IP, reserve a Standard Public IP and uncomment the `loadBalancerIP` +
  resource-group annotation in `frontend.yaml`.
- **Backend is internal** (ClusterIP) - only the frontend reaches it (`http://backend:8000`).
- **`ingress.optional.yaml`** is NOT used with the LoadBalancer approach. It's kept only if you
  ever prefer host-based routing on the shared ingress IP (then switch the Service back to ClusterIP).
- **Isolation / rollback:** everything is in `compliance-v2`; `default` is unaffected.
  Roll back with `kubectl delete namespace compliance-v2` (also releases the LoadBalancer IP).
- **Single replicas:** `auth.db` is local SQLite. Scale the backend only after moving it to
  Postgres/Azure SQL; for frontend >1 add `sessionAffinity: ClientIP`.
