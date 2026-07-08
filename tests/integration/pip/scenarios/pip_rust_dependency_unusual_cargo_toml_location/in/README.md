# A test for unusual location of Cargo.toml

Most Python Rust extension packages come with a top-level Cargo.toml file, but
not all. For those which do not our current heuristic is to look for the
nearest Cargo.toml file reachable from the package's top-level directory.  This
is a regression test to ensure we adhere to that heuristic and do not pick a
Cargo.toml file from the list of found ones (e.g. in examples) arbitrarily.  It
utilises a specific version of cryptography which is known for an unusual
(compared to other versions and packages) placement of Cargo.toml.
