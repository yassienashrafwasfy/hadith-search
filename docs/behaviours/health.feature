# Verified against: backend/routers/root.py, tests/test_health.py, tools/deploy.sh (smoke check)
Feature: Health check
  GET /api/v1/health tells a load balancer or tools/deploy.sh whether this app can serve requests.

  Scenario: A running app with a reachable database is healthy
    Given the database is reachable
    When a client calls GET /api/v1/health without a token
    Then the status is 200
    And the body status is "ok"
    And the answer is never cached

  Scenario: An unreachable database makes the app not ready
    Given the database is not reachable
    When a client calls GET /api/v1/health without a token
    Then the status is 503
    And the content type is application/problem+json
    And the answer is never cached
    And the body does not mention the database address

  Scenario: The API root links to the health check
    Given the database is reachable
    When a client calls GET /api/v1 without a token
    Then the status is 200
    And the root lists a health link
