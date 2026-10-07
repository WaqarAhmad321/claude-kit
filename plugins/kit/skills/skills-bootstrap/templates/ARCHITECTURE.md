# {Module}: architecture

## What it does
{2-3 sentences: what it does, in product terms.}

## Who uses it
- {Role}: {what they can do}

## How it works
```mermaid
flowchart LR
    UI[{UI}] --> API[{API}]
    API --> Service[{Service}]
    Service --> Store[({Data store})]
```
{A short walk-through of the main flow(s), start to end, in plain sentences.}

## Data
{What it stores and owns, in plain words.} Schema: [SCHEMA.md](SCHEMA.md).

## Connections
{Modules and outside services it calls or is called by, in plain words.} Edges: [INTERLINKING.md](INTERLINKING.md).

## Key decisions
- {Why it's built this way, one line}. {ADR link, if there is one.}

## Rules and limits
- {A business rule, a permission, or a limit, and what happens when it's hit.}

## How it's tested
{The journeys that cover it.} Flows: [FLOWS.md](FLOWS.md).

## Where the code lives
- `{path}`: {what's there}
