# DNF server TLS client authentication test

This scenario covers pre-fetch of RPMs from an authenticated RPM repository
server (more specifically a DNF server) referenced by the rpms.lock.yaml file
using CLI extra backend options to pass file paths from which to load the TLS
client certificates.
This test requires the local Nexus proxy instance to run.
