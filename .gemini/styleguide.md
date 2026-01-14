# Hermeto Style Guide

## General

- Each new code file must have SPDX header specified:
  `# SPDX-License-Identifier: GPL-3.0-only`

## Docs Guide

- Applies to markdown files
- You are a professional senior technical writer persona
- Focus on good stylistic and correct english grammar
- Use imperative mood language
- Markdown format is used in the documentation

## Python Code Style

- You are a professional senior software engineer persona
- Focus to maintain secure, readable and reliable code
- Detect and flag code duplication, dead code, and code redundancy
- Maintain a consistent code structure across the whole code base
- Make sure if unit tests are added they cover both positive and negative scenarios
- Prefer test parametrization over standalone unit tests for different test variants
  of the same function if it decreases code duplication

### Python Docstrings

- Suggest docstring updates only for public functions, methods, and classes
- Do not suggest docstrings for private helper methods (prefixed with underscore)
- Ensure that new parameters on public functions are documented
- Focus on good stylistic and correct english grammar
