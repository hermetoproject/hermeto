FROM mirror.gcr.io/maven:3.9-eclipse-temurin-21

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src

RUN . /tmp/hermeto.env && mvn package

# During the build, the source code is only mounted, not copied.
# This e2e test uses CMD that requires the source code to be present.
RUN cp -r /src /app

CMD ["java", "-jar", "/app/target/demo-0.0.1-SNAPSHOT.jar"]
