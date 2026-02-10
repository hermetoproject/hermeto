FROM mirror.gcr.io/eclipse-temurin:25-jre-alpine

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

RUN cp -r /tmp/hermeto-output/deps/generic/ /deps

CMD ["java", "-cp", "/deps/ant.jar:/deps/ant-launcher.jar", "org.apache.tools.ant.Main", "-version"]
