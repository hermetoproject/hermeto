FROM mirror.gcr.io/golang:1.20.0-alpine

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src/twenty

RUN . /tmp/hermeto.env && go build -o /usr/bin/twenty

CMD twenty
