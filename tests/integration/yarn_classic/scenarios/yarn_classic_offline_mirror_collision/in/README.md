# Offline mirror collision test

Two URL dependencies whose tarballs share the same filename (example.tgz)
but contain different packages. Hermeto must detect the collision and reject
the request. The tarballs are served from a local Nexus raw hosted repository.
