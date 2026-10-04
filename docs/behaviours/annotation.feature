# Verified against: backend/routers/annotation.py, tests/test_routers.py
Feature: Annotation flow
  An annotator judges hadiths pooled for the queries assigned to them. Every route needs a token.

  Background:
    Given annotator "alice" has signed up and holds a token
    And "alice" is assigned queries q1 and q2, each with a pool of 2 hadiths

  Scenario: List assignments with progress
    When alice sends GET /api/v1/assignments
    Then each assignment shows query_id, total, graded and current_index
    And q1 shows total 2 and graded 0

  Scenario: Open an assignment
    When alice sends GET /api/v1/assignments/q1
    Then the body has the query text, the pooled hadiths with their texts, her labels and current_index
    And the links include a templated "label" link

  Scenario Outline: Assignments that are not hers do not exist
    When alice asks for /api/v1/assignments/<query_id>
    Then the status is 404

    Examples:
      | query_id |
      | q3       |
      | nope     |

  Scenario: Assignments need a token
    When a client calls GET /api/v1/assignments without a token
    Then the status is 401

  Scenario: Labelling is idempotent
    When alice puts label 0 on hadith 1 of q1
    Then the status is 201
    When she puts label 2 on the same hadith
    Then the status is 200 and the stored label is 2

  Scenario: Invalid labels and hadiths outside the pool are refused
    When alice puts label 5 on a hadith of q1
    Then the status is 422
    When she labels a hadith that is not in the pool of q1
    Then the status is 404

  Scenario: Progress is saved inside the pool
    When alice puts progress index 1 on q1
    Then the status is 200 and GET /api/v1/assignments/q1 shows current_index 1
    When she puts index 9 or -1
    Then the status is 422

  Scenario: Agreement summary
    When alice sends GET /api/v1/agreement
    Then the body has per_query, overall and _links
