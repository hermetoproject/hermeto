# User-Agent test

This scenario checks that Hermeto identifies itself with a descriptive
User-Agent instead of a generic HTTP library default (e.g. `python-requests/x.y`
or `aiohttp/x.y`), which registries such as Maven Central may block.
The RPM is fetched through a dedicated local Nexus proxy endpoint that rejects
generic User-Agents with 403, so a failure here means the User-Agent regressed.
This test requires the local Nexus proxy instance to run.
