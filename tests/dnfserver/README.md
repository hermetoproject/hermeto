To start the testing nginx DNF repo container use our test helper script:
Note: make sure you have `createrepo_c` installed (hermeto has a hard dependency on this)
```
$ start.sh
```

Check that it is answering for a basic HTTP request
```
$ curl http://localhost:8081
```

Check that tls client auth is working
```
$ curl --insecure https://localhost:8443
```

You should receive:
`400 No required SSL certificate was sent`

Try sending our test SSL client certificates and download the test RPM from the server

```
$ curl -O \
  --cacert certificates/CA.crt \
  --key certificates/client.key \
  --cert certificates/client.crt \
  https://localhost:8443/pkg/redhat-release-9.4-0.5.el9.x86_64.rpm
```

In order to re-generate the set of SSL certificates
```
$ cd certificates
$ ./generate-certs.sh
```
