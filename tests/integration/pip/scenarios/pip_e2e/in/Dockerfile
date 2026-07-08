FROM registry.access.redhat.com/ubi9/python-39@sha256:01fe0c6b483a3c425a1b851569b4f6445244c95e16c195a4f52c6e860e82891b

# Test disabled network access
RUN if curl -IsS www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /opt/test_package_cachi2
COPY . .

RUN pip install -r requirements.txt

CMD ["python", "/opt/test_package_cachi2/src/test_package_cachi2/main.py"]
