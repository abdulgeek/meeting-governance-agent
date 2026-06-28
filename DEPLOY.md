# AWS ECS Fargate deployment

Both APIs run on **ECS Fargate (ARM64)** behind one Application Load Balancer with **HTTPS** on
`velocrux.com` subdomains. Region `us-east-1`, account `502339147927`.

## Live endpoints (HTTPS)
| Service | URL |
|---------|-----|
| NestJS API | `https://api.velocrux.com` |
| Python engine | `https://engine.velocrux.com` (WebSockets: `wss://engine.velocrux.com/ws`, `…/recall`) |

`http://` → `https://` (301). Real ACM cert, so no `-k`. Verified: nest `/health` 200, engine `/`
200, engine `/summarize` returns a Bedrock summary, NestJS↔MongoDB Atlas connected.

## Frontend (Vercel project → Environment Variables)
```
NEXT_PUBLIC_API_URL=https://api.velocrux.com
NEXT_PUBLIC_WS_URL=wss://engine.velocrux.com/ws
```

## DNS (in Vercel, on velocrux.com) — already added
| Name | Type | Value |
|------|------|-------|
| `api` | CNAME | `governance-alb-2103252488.us-east-1.elb.amazonaws.com` |
| `engine` | CNAME | `governance-alb-2103252488.us-east-1.elb.amazonaws.com` |
| `_fb5a1f71…` | CNAME | `_91af3c97….acm-validations.aws` (ACM validation) |
| `@` | CAA | `0 issue "amazon.com"` (so ACM may issue; added alongside Vercel's existing CAAs) |

## TLS / ALB
- ACM wildcard cert `*.velocrux.com` (DNS-validated).
- ALB `governance-alb`: **`:443` HTTPS** listener (TLS13) → default forward **nest TG**, rule
  `Host=engine.velocrux.com` → **engine TG**; **`:80`** redirects to 443. (Old `:8080` removed.)
- ALB SG allows inbound 80 + 443.

## What was created (us-east-1)
- **ECR:** `governance-engine`, `governance-nest` (arm64)
- **ECS:** cluster `governance-cluster`; services `governance-engine-svc` (1 vCPU/2 GB),
  `governance-nest-svc` (0.25 vCPU/0.5 GB); task defs `governance-engine`, `governance-nest`
- **IAM:** `ecsTaskExecutionRole`; `governance-engine-task-role` (inline `bedrock:InvokeModel` —
  the engine calls Bedrock via this role, **no static AWS keys in the container**)
- **SGs:** `governance-alb-sg` (80/443), `governance-task-sg` (4000/8000 from ALB)
- **Logs:** `/ecs/governance-engine`, `/ecs/governance-nest`
- Tasks: default-VPC public subnets + public IP (no NAT)
- Service env (set on the task definitions): engine `PUBLIC_BASE_URL=https://engine.velocrux.com`,
  `NEST_API_URL=https://api.velocrux.com`, Deepgram/Recall keys, model id; nest `MONGO_URI`,
  `JWT_SECRET`, `PYTHON_ENGINE_URL=https://engine.velocrux.com`. MongoDB Atlas Network Access
  must allow the Fargate egress (set to `0.0.0.0/0`).

Env is inline in the task definitions. Hardening follow-up: move secrets to AWS Secrets Manager
and reference via the task def `secrets` block.

## Redeploy after a code change
```bash
source /tmp/govdeploy.env   # REGISTRY, REGION, CLUSTER
docker build --platform linux/arm64 -t $REGISTRY/governance-engine:latest python-api
docker push $REGISTRY/governance-engine:latest
aws ecs update-service --region $REGION --cluster $CLUSTER \
  --service governance-engine-svc --force-new-deployment
```
(Nest is the same with `governance-nest` / `nest-api`.) Env changed? Re-register the task def with
the new env and `update-service --task-definition governance-<svc>`.

## Rough cost
~1 ALB (~$16/mo) + 2 Fargate tasks (~$45–55/mo) + ECR + egress ≈ **~$65–80/mo** running.
`aws ecs update-service --desired-count 0` on both services pauses compute when idle.
