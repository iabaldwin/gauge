# Vertical rubric

Classify each company from its YC `one_liner` and `long_description` only. Tags, industry, and subindustry are hints, not labels. YC's own tags are too coarse to use as the vertical.

A vertical is one buyer, one job, and one workflow. "Prior-auth automation for specialty pharmacies" is the right grain. "Healthcare AI" is too coarse. The company's product name is too fine.

## Output

JSONL, one object per company, no markdown. Every slug in the input appears exactly once.

```json
{"slug":"servo7","buyer":"warehouses","user":"warehouse operators","job":"unload inbound containers","domain":"warehouse-robotics","vertical":"container unloading robots for warehouses","modality":"hardware"}
```

## Fields

- `buyer`: who pays. Lowercase, no article, 2–5 words. An org type or role, not "businesses" or "companies".
- `user`: who operates it day to day. Same style. May match the buyer.
- `job`: the outcome, not the technology. Lowercase verb phrase, 2–6 words. Do not start with "use AI" or "automate".
- `domain`: exactly one id from the list below.
- `vertical`: lowercase noun phrase, about 4–8 words, shaped like "{workflow} for {buyer}". No company name. No "AI-powered" or "AI-native". Keep "agent" only when the product is an autonomous worker rather than a tool or model.
- `modality`: one of `software`, `marketplace`, `hardware`, `bio`, `services`.

Two companies in the same market must be able to share a vertical string. Prefer a plain workflow name over a slogan.

If the description is thin, classify from the one-liner and do not invent a market the text does not support.

## Grain

Too coarse, do not use:

- coding agents for engineers
- AI infrastructure for companies
- robots for industry
- software for hospitals
- agents for enterprises

In range:

- cloud dev environments for coding agents
- incident triage agents for engineering teams
- container unloading robots for warehouses
- claim inventory automation for property insurers
- emr systems for medspas

If a phrase would cover more than about 15 companies in this window, it is too coarse. Split on the workflow or the buyer.

## Domain ids

Use these strings exactly.

Software infrastructure:

- `coding-agents` — agents that write, review, or maintain software
- `agent-runtime` — sandboxes, orchestration, memory, tool routing, agent hosting
- `agent-evals` — evals, traces, guardrails, reliability for agents or models
- `inference-serving` — inference clouds, compression, serving, on-device model runtime
- `training-data` — training data, RL environments, sims used to train models (not robot bodies)
- `developer-tools` — devtools that are not themselves coding agents (CI, feature flags, APIs, IDEs for humans)
- `site-reliability` — incident response, SRE, production debugging
- `data-infrastructure` — warehouses, pipelines, data quality, search indexes
- `voice-speech` — voice models, speech infra, voice agents as a platform
- `computer-use` — agents that drive existing UIs or desktops across many workflows
- `security` — appsec, cloud security, soc, red team
- `identity-fraud` — identity, fraud, financial crime
- `semiconductors` — chips, interconnect, compute hardware

Go-to-market and office work:

- `sales-gtm` — pipeline, outbound, CRM, revenue agents
- `customer-support` — support, success, customer ops
- `marketing-ads` — ads, content ops, lifecycle marketing
- `recruiting` — hiring, staffing, HR
- `finance-ops` — accounting, close, fp&a, audit, expenses
- `insurance-ops` — claims, underwriting, policy admin, insurer back office
- `banking-lending` — banks, lenders, credit, asset management ops
- `payments` — payments, billing, treasury, agent commerce rails
- `legal` — law firms and legal workflows, including IP prosecution
- `compliance-risk` — compliance, risk, internal controls outside a single vertical above
- `document-workflows` — generic enterprise document processing with no stronger vertical

Care and science:

- `clinical-ops` — care delivery, clinics, staffing, patient engagement
- `clinical-admin` — billing, coding, prior auth, RCM, medical records admin
- `life-sciences` — pharma R&D, lab software, clinical trials, bio data
- `biotech` — therapeutics, diagnostics, wet lab, when the product is biological rather than software

Physical world:

- `engineering-design` — CAD, mechanical, electrical, firmware, hardware design tools
- `manufacturing-ops` — factory software, production planning, quality systems
- `warehouse-robotics` — robots whose job is inside a warehouse or fulfillment building
- `humanoid-robotics` — general-purpose or humanoid robots, robot foundation models, robot brains
- `robot-components` — actuators, sensors, batteries, parts sold to robot builders
- `industrial-inspection` — visual inspection, damage detection, machine vision on a line
- `construction` — AEC, job sites, building
- `logistics` — freight, fleet, supply chain, not the warehouse robot itself
- `energy-climate` — energy, grid, climate, batteries-as-energy
- `defense` — defense and dual-use military
- `aviation-space` — aviation ops, spacecraft, satellites
- `drones` — aerial drones
- `automotive` — vehicles, dealers, auto repair, self-driving sensors

Other markets:

- `real-estate` — property, brokerage, property management
- `home-services` — home services and other local field businesses (HVAC, repair, landscaping)
- `hospitality` — hotels, restaurants, events
- `retail-commerce` — retail, ecommerce, CPG, pricing
- `wholesale-distribution` — distributors and wholesalers
- `education` — schools, tutoring, learning products
- `consumer-apps` — consumer products with no stronger vertical
- `media-production` — video, audio, image production tools
- `agriculture` — farms and food production
- `gaming` — game studios and game ops
- `government` — public sector buyers
- `scientific-tools` — science infra that is not life-sciences or biotech

Pick the domain of the buyer and the workflow, not the fact that the product uses a model. A coding agent for mechanical engineers is `engineering-design`, not `coding-agents`. A voice agent sold as infra to other developers is `voice-speech`. A voice agent that answers hotel calls is `hospitality`.

## Worked examples

These show grain. Re-read the company text before copying them.

- Servo7, "Container unloading robots for warehouses" → buyer `warehouses`, user `warehouse operators`, job `unload inbound containers`, domain `warehouse-robotics`, vertical `container unloading robots for warehouses`, modality `hardware`.
- Cardinal, "Revenue Agents for GTM teams" → buyer `gtm teams`, user `sales reps`, job `generate pipeline`, domain `sales-gtm`, vertical `outbound revenue agents for gtm teams`, modality `software`.
- IncidentFox, "AI SRE agent that triages production incidents" → buyer `engineering teams`, user `on-call engineers`, job `triage production incidents`, domain `site-reliability`, vertical `incident triage agents for engineering teams`, modality `software`.
- InventoryQuant, "automate the inventory process in insurance" → buyer `property insurers`, user `claims adjusters`, job `complete claim inventories`, domain `insurance-ops`, vertical `claim inventory automation for property insurers`, modality `software`.
- Tepali, "AI EMR for medspas & wellness clinics" → buyer `medspas`, user `clinic staff`, job `keep clinical records`, domain `clinical-ops`, vertical `emr systems for medspas`, modality `software`.
- Aurorin CAD, "Claude code for Mechanical Engineers" → buyer `mechanical engineers`, user `mechanical engineers`, job `generate mechanical designs`, domain `engineering-design`, vertical `design agents for mechanical engineers`, modality `software`.
