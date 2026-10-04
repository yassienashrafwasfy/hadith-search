# Verified against: backend/settings.py, backend/main.py, tests/test_settings.py, docker-compose.yml
Feature: Production mode
  APP_ENV is "dev" (default), "test" or "prod".

  Scenario: Prod hides the interactive docs and the schema
    Given APP_ENV is "prod" with a valid AUTH_SECRET and CORS_ORIGINS
    When the app is created
    Then docs_url, redoc_url and openapi_url are all None

  Scenario: Dev keeps the docs
    Given APP_ENV is not set
    When the app is created
    Then docs_url is "/docs" and openapi_url is "/openapi.json"

  Scenario Outline: Prod will not start without its secrets
    Given APP_ENV is "prod" and <missing> is not set
    When Settings are built
    Then a validation error is raised

    Examples:
      | missing     |
      | AUTH_SECRET |
      | CORS_ORIGINS |

  Scenario Outline: Prod refuses a wildcard CORS origin
    Given APP_ENV is "prod" and CORS_ORIGINS is "<origins>"
    When Settings are built
    Then the error says CORS_ORIGINS must not be *

    Examples:
      | origins                    |
      | *                          |
      | https://hadith.example, *  |

  Scenario: Dev accepts a wildcard CORS origin
    Given APP_ENV is "dev" and CORS_ORIGINS is "*"
    When Settings are built
    Then no error is raised

  Scenario: Prod still checks the secret length
    Given APP_ENV is "prod" and AUTH_SECRET is "short"
    When Settings are built
    Then the error says the secret needs at least 32 characters

  Scenario: Test behaves like dev
    Given APP_ENV is "test" and AUTH_SECRET is not set
    When the app is created
    Then it starts, like in dev

  Scenario: An unknown APP_ENV is refused
    Given APP_ENV is an unknown value
    Then Settings refuses it

  Scenario: Settings are read once
    When get_settings is called twice
    Then both calls return the same object until get_settings.cache_clear() runs
