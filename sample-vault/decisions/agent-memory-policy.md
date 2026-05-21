---
title: Agent Memory Policy
kind: decision
visibility: public
summary: Demo decision for capture-first agent memory.
status: accepted
---
# Agent Memory Policy

## Context

Agents need a place to store observations, but not every observation should
become canonical service documentation immediately.

## Decision

Agents write uncertain or newly discovered facts as captures first. Reviewed
captures can be promoted into pages such as [[API Gateway|services/api-gateway]]
or [[Service Dashboard|services/service-dashboard]].

## Consequences

The vault keeps a clear separation between draft memory and accepted knowledge.
The [[Capture Workflow|guides/capture-workflow]] guide describes the operating
process.
