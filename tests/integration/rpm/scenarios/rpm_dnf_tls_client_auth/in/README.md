# Cachi2 DNF server TLS client authentication test

This scenario covers pre-fetch of RPMs from an authenticated RPM repository server (more
specifically a DNF server) referenced by the rpms.lock.yaml file using CLI extra backend options
to pass file paths from which to load the TLS client certificates.

The test server must be running for this test case to pass and pre-installed with the expected CA
and server TLS certificates. For more details on the test server setup, see
https://github.com/containerbuildsystem/cachi2/tree/main/tests/dnfserver.
