---
sidebar_position: 1
title: Overview
description: What Agent Red Customer Experience does, how the closed-beta pipeline works, and the infrastructure it runs on.
---

# Platform Overview

Agent Red Customer Experience is in a closed beta. It automates customer support for e-commerce stores. The closed beta runs seven core roles in one gateway process: intent classification, knowledge retrieval, response generation, a critic, escalation, analytics, and a co-pilot for staff. Peer agents are not part of the beta. To request access, email [support@remakerdigital.com](mailto:support@remakerdigital.com).

![Agent Red admin dashboard](/img/admin/dashboard.png)

## The agent pipeline

When a customer sends a message, it flows through the agent pipeline:

```mermaid
flowchart LR
    A[Customer Message] --> B[Intent\nClassification]
    B --> C[Knowledge\nRetrieval]
    C --> D[Response\nGeneration]
    D --> E[Critic /\nSupervisor]
    E --> F{Safe?}
    F -- Yes --> G[Deliver\nResponse]
    F -- No --> D
    B --> H[Escalation\nDetection]
    H -- Human needed --> I[Route to\nAgent]
    B --> J[Analytics]
```

1. **Intent Classification.** Determines what the customer needs (order status, return request, product question, and others) across 18 total intent categories (17 customer-facing plus an admin-assistance route for admin-authenticated traffic) using a smaller language model.
2. **Knowledge Retrieval** — Searches your product catalog, FAQ database, and policy documents using hybrid semantic vector + keyword search to find relevant context.
3. **Response Generation** — Composes a natural-language reply using the retrieved context and conversation history, personalized to your brand voice.
4. **Critic / Supervisor** — Validates the response for factual accuracy, policy compliance, and content safety before it reaches the customer.
5. **Escalation Detection** — Evaluates whether the conversation requires human attention (angry customer, complex issue, VIP account) and routes accordingly.
6. **Analytics** — Records conversation metrics for quality monitoring, reporting, and continuous improvement.

## Architecture

The closed beta runs as one gateway process. The seven core roles run in that process. Customer data is stored with tenant-level isolation. Customer memory is off for the closed beta: the product does not search earlier conversations, and it does not turn a completed conversation into vectors. Knowledge retrieval still runs.

```mermaid
graph TB
    subgraph Internet
        CU[Customer]
    end

    subgraph Azure["Azure (East US)"]
        API[API Gateway\nAzure Container Apps]
        API --> IC[Intent Classifier]
        API --> KR[Knowledge Retrieval]
        API --> RG[Response Generator]
        API --> CS[Critic / Supervisor]
        API --> ESC[Escalation]
        API --> AN[Analytics]

        MEM[(Customer Memory\nProfiles + Vectors)]
        DB[(Cosmos DB\nServerless)]
        RD[(Redis Cache)]
        KV[Key Vault]
        AI[Language models]
    end

    CU -->|HTTPS| API
    IC & KR & RG & CS & ESC --> AI
    IC & KR & RG & AN --> DB
    RG --> MEM
    API --> KV
    API --> RD
```

| Component | Technology |
|---|---|
| Agent runtime | Azure Container Apps (native auto-scaling) |
| Agent communication | HTTP (in-process, synchronous pipeline) |
| Database | Azure Cosmos DB (Serverless, DiskANN vector index) |
| Cache | Azure Cache for Redis |
| AI models | Language models for replies, classification, and embeddings |
| Embeddings | text-embedding-3-large |
| Secrets | Azure Key Vault (Managed Identity) |
| Customer Memory | Cosmos DB (profiles + vectorized conversation transcripts) |
| Monitoring | OpenTelemetry (Application Insights) |

## Design targets

These are the performance targets Agent Red is designed to achieve:

| Metric | Target |
|---|---|
| Response latency (P95) | < 2 seconds |
| Uptime SLA (Enterprise) | 99.95% |
| Concurrent tenants at launch | 680 |
| Rate limit (all tiers) | 300 requests per minute per tenant |

## Next steps

- [How It Works](./how-it-works): the core pipeline, communication, and data flow.
- [Initial Setup](./setup) — What you need to get Agent Red running for your store.

---

*© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.*
