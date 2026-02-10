FROM mirror.gcr.io/python:3.8-alpine

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src
RUN . /tmp/hermeto.env && python3 -m pip install -r requirements.txt

# During the build, the source code is only mounted, not copied
# Some integration tests use CMD that requires the source code to be present
RUN cp -r /src /app
WORKDIR /app
