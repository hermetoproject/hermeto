# User-Agent test

This scenario checks that Hermeto identifies itself in the User-Agent instead
of sending a generic HTTP library default (e.g. `python-requests/x.y` or
`aiohttp/x.y`), which registries such as Maven Central may block.
The artifact is fetched through a dedicated local Nexus endpoint that rejects
any User-Agent not containing `hermeto/` with 403, so a failure here means the
User-Agent regressed. The artifact is uploaded to a Nexus raw hosted repository
by the test itself. This test requires the local Nexus instance to run.
