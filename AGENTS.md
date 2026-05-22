# AGENTS.md

## Overview

This repository is a **Spring Cloud Config Server configuration store** — it contains only YAML configuration files that are served by a separate Spring Cloud Config Server to microservice clients at runtime. There is no application source code, build system, or dependencies in this repository itself.

### Config files

| File | Purpose |
|------|---------|
| `application.yml` | Shared base Spring config with `dev` and `test` profiles |
| `microservicecloud-config-client.yml` | Config-client service (ports 8201/8202, Eureka URLs) |
| `microservicecloud-config-dept-client.yml` | Department service (port 8001, MySQL/MyBatis/Druid, Eureka) |
| `microservicecloud-config-eureka-client.yml` | Eureka Server (port 7001, self-registration settings) |

## Cursor Cloud specific instructions

### Linting

Install `yamllint` via pip and validate all YAML files:

```sh
pip3 install --user yamllint
export PATH="$HOME/.local/bin:$PATH"
yamllint -d relaxed /workspace/*.yml
```

Pre-existing lint warnings (indentation, trailing spaces, missing trailing newlines) are part of the original codebase. All files parse successfully as valid multi-document YAML.

### Testing configs with a Spring Cloud Config Server

This repo uses legacy `spring.profiles` syntax (Spring Boot 2.x era). To serve these configs locally, you must use **Spring Boot 2.7.x** with **Spring Cloud 2021.0.x**. Spring Boot 3.x rejects the `spring.profiles` property and will fail with `InvalidConfigDataPropertyException`.

A minimal Config Server project is available at `/tmp/config-server/` (built during env setup). To start it:

```sh
cd /tmp/config-server
java -jar target/config-server-0.0.1-SNAPSHOT.jar
```

It starts on port 3344 and reads from `file:///workspace` (this local git repo, `master` branch).

Fetch configs via REST API pattern: `http://localhost:3344/{application}/{profile}/{label}`

Examples:
```sh
curl http://localhost:3344/application/dev/master
curl http://localhost:3344/microservicecloud-config-client/dev/master
curl http://localhost:3344/microservicecloud-config-dept-client/dev/master
curl http://localhost:3344/microservicecloud-config-eureka-client/test/master
```

### Important caveats

- The Config Server must be restarted (or the endpoint re-fetched) to pick up new commits to the YAML files, since it reads from the Git repo.
- The `{label}` in the REST URL corresponds to the Git branch name — use the branch you're working on, not just `master`.
- Maven (`mvn`) is required to rebuild the Config Server if the project is modified; it's installed via `sudo apt-get install -y maven`.
