---
slug: /
sidebar_position: 1
title: Welcome
hide_title: true
---

import ThemedImage from '@theme/ThemedImage';

<div style={{textAlign: 'center', marginBottom: '2rem'}}>
  <ThemedImage
    alt="Agent Red"
    sources={{
      light: '/img/primary-logo-no-wordmark_black_text.svg',
      dark: '/img/primary-logo-no-wordmark_white_text.svg',
    }}
    style={{maxHeight: '240px', marginTop: '100px', marginBottom: '80px'}}
  />
  <h1 style={{fontSize: '2.5rem', marginBottom: '0.25rem'}}>Agent Red Customer Experience</h1>
  <p style={{fontSize: '1.1rem', opacity: 0.7}}>
    A product of <a href="https://remakerdigital.com" target="_blank" rel="noopener noreferrer">Remaker Digital</a>
  </p>
</div>

Agent Red Customer Experience is in a closed beta. It automates support conversations for e-commerce stores. Access is by request. Email [support@remakerdigital.com](mailto:support@remakerdigital.com).

The public source is [agent-red-customer-experience](https://github.com/Remaker-Digital/agent-red-customer-experience). It is source-available under the PolyForm Noncommercial License 1.0.0. It is not open source.

## What you'll find here

- **[Getting started](/docs/getting-started/overview)** — Understand what Agent Red does, how it works, and how to set up your first deployment.
- **[Admin guide](/docs/admin-guide)** — Complete reference for every setting in the admin console — what each input does, how changing it affects system behavior, and best practices.
- **[Integrations](/docs/integrations/shopify)** — Connect Agent Red to Shopify and other platforms.
- **[Billing](/docs/billing/billable-conversation-spec)** — How conversations are metered and billed.
- **[Changelog](/docs/changelog)** — What's new in each release.

## Key capabilities

| Capability | Description |
|------------|-------------|
| **Intent classification** | Routes customer queries across 18 intent categories (17 customer-facing plus 1 admin-only route) |
| **Knowledge retrieval** | Searches products, FAQs, and policies using hybrid vector + keyword search |
| **Response generation** | Crafts personalized, brand-consistent replies with a language model |
| **Escalation detection** | Identifies conversations requiring human intervention |
| **Analytics** | Monitors conversation quality, resolution rates, and AI performance |
| **Content safety** | Validates every response before delivery to customers |
| **Persistent customer memory** | Off for the closed beta. The product does not search earlier conversations, and it does not turn a completed conversation into vectors. |
| **Customer identity** | Pre-chat form, OTP email verification, and Shopify HMAC passthrough ensure every conversation is linked to a real customer |
| **Automated onboarding** | Widget keys and a setup wizard, after access is granted |
| **MCP integrations** | Connects to Stripe and Shopify Storefront via the Model Context Protocol for real-time payment and product data |
| **Admin security** | API key authentication, magic-link sign-in, and optional two-factor verification for admin-console access |

## Planned pricing

These prices are planned. They are not on offer yet. There is no self-serve sign-up and no free trial.

| Tier | Included conversations | Planned price |
|------|----------------------|-------------|
| Starter | 1,000/month | $149/month |
| Professional | 5,000/month | $399/month |
| Enterprise | 20,000/month | $999/month |

The closed beta runs seven core roles in one gateway process: intent classification, knowledge retrieval, response generation, a critic, escalation, analytics, and a co-pilot for staff. Peer agents are not part of the beta.

To request access, email [support@remakerdigital.com](mailto:support@remakerdigital.com). [Shopify](/docs/integrations/shopify) is one way a store can connect after access is granted.

## Need help?

- **Support:** [support@remakerdigital.com](mailto:support@remakerdigital.com)
- **Install:** [Shopify integration guide](/docs/integrations/shopify)

---

*© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.*
