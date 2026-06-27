# AWS ECS Fargate deployment

Both APIs run on **ECS Fargate (ARM64)** behind one Application Load Balancer, in `us-east-1`,
account `502339147927`.

## Live endpoints
| Service | URL | Status |
|---------|-----|--------|
| Python engine | `http://governance-alb-2103252488.us-east-1.elb.amazonaws.com:8080` | ✅ live (verified: `/`, `/summarize` via Bedrock) |
| NestJS API | `http://governance-alb-2103252488.us-east-1.elb.amazonaws.com` (`:80`) | ⏳ running, blocked on the Atlas allowlist (below) |

## ⚠️ One action required — MongoDB Atlas allowlist
NestJS is deployed and running but can't reach Atlas yet:
`MongooseServerSelectionError: … IP that isn't whitelisted`. Fargate's egress IP isn't on your
Atlas cluster's Network Access list (and it changes per task, so don't pin one IP).

**Fix:** MongoDB Atlas → **Network Access → Add IP Address → `0.0.0.0/0`** ("allow from anywhere").
The running task is retrying the connection, so once you save it NestJS connects and the target
goes healthy automatically (no redeploy needed). *(Tighter option: put the tasks behind a NAT
gateway with an Elastic IP and allowlist that one EIP — extra ~$32/mo.)*

## What was created (for reference / cleanup)
- **ECR:** `governance-engine`, `governance-nest` (arm64 images)
- **ECS:** cluster `governance-cluster`; services `governance-engine-svc` (1 vCPU/2 GB),
  `governance-nest-svc` (0.25 vCPU/0.5 GB); task defs `governance-engine`, `governance-nest`
- **ALB:** `governance-alb` — listener `:80`→nest TG, `:8080`→engine TG (HTTP)
- **IAM:** `ecsTaskExecutionRole`; `governance-engine-task-role` (inline `bedrock:InvokeModel` —
  the engine calls Bedrock via this role, **no static AWS keys in the container**)
- **SGs:** `governance-alb-sg` (80/8080 from internet), `governance-task-sg` (4000/8000 from ALB)
- **Logs:** `/ecs/governance-engine`, `/ecs/governance-nest`
- Tasks run in the **default VPC public subnets with public IPs** (no NAT gateway → lower cost)

Secrets (DEEPGRAM/RECALL/JWT/MONGO_URI) are set as task-definition env vars. Hardening follow-up:
move them to AWS Secrets Manager and reference via the task def `secrets` block.

## Redeploy after a code change
```bash
source /tmp/govdeploy.env   # REGISTRY, REGION, CLUSTER
# rebuild + push (engine shown; nest is the same with governance-nest / nest-api)
docker build --platform linux/arm64 -t $REGISTRY/governance-engine:latest python-api
docker push $REGISTRY/governance-engine:latest
aws ecs update-service --region $REGION --cluster $CLUSTER \
  --service governance-engine-svc --force-new-deployment
```
Env changed? Re-register the task def with the new env and `update-service --task-definition`.

## Follow-up: HTTPS / domain (needed for the full product)
Today the ALB is **HTTP**. Two things need TLS:
- **Recall** connects to `wss://…/recall` → needs `https`/`wss` (TLS).
- The **Vercel frontend** is `https://` → it can't call an `http://` ALB (browser mixed-content).

To finish: point a domain at the ALB (Route 53 or a CNAME), request an **ACM cert**, add **HTTPS
listeners** (`443`→nest TG, e.g. `8443`→engine TG), then set the frontend
`NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL` and the engine `PUBLIC_BASE_URL` to the `https`/`wss`
domain. (Engine `NEST_API_URL` and nest `PYTHON_ENGINE_URL` are server-to-server and can stay on
the internal ALB.)

## Rough cost
~1 ALB (~$16/mo) + 2 Fargate tasks (~$45–55/mo combined at this size) + ECR storage + egress ≈
**~$65–80/mo** while running. `aws ecs update-service --desired-count 0` for both services pauses
compute when idle.
