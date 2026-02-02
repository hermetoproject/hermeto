#!/bin/bash -e
rm -f ./*.{key,crt}
tempssldir=$(mktemp -d)

# generate CA cert
openssl ecparam -genkey -name prime256v1 -out CA.key
openssl req -new -x509 -key CA.key -days 36500 -out CA.crt -subj "/CN=CA" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "basicConstraints=critical,CA:TRUE"

# generate client/server key, certificate signing request and certificate
for entity in client server; do
    openssl ecparam -genkey -name prime256v1 -out ${entity}.key
    CN=$([ "${entity}" == "client" ] && echo "${entity}" || echo "127.0.0.1")
    SAN=$([ "${CN}" == "client" ] && echo DNS || echo IP):"${CN}"

    openssl req \
        -quiet \
        -new \
        -x509 \
        -noenc \
        -key "${entity}.key" \
        -days 36500 \
        -CA CA.crt \
        -CAkey CA.key \
        -out "${entity}.crt" \
        -subj "/CN=${CN}" \
        -addext "subjectAltName=${SAN}" \
        -addext "basicConstraints=critical,CA:FALSE" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment"
done
rm -rf "${tempssldir}"
