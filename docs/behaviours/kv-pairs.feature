# Verified against: backend/routers/kv_pairs.py, tests/test_routers.py
Feature: Verify key-value pairs
  Every kv-pairs route needs a token. Test data: three pairs, topics prayer (2) and fasting (1),
  statuses pending (2) and verified (1).

  Background:
    Given annotator "alice" holds a token

  Scenario Outline: kv-pairs routes need a token
    When a client calls <method> /api/v1/kv-pairs<path> without a token
    Then the status is 401

    Examples:
      | method | path        |
      | GET    |             |
      | GET    | /statistics |
      | PATCH  | /1          |
      | PATCH  |             |

  Scenario: List with filters and paging
    When alice lists kv-pairs
    Then total is 3
    When she filters by status "verified"
    Then total is 1
    When she filters by topic "prayer"
    Then total is 2
    When she asks for limit=1 and offset=1
    Then she gets pair 2 and the links self, first, last, prev and next

  Scenario Outline: Bad paging is refused
    When alice lists kv-pairs with <query>
    Then the status is 422

    Examples:
      | query      |
      | limit=0    |
      | limit=1000 |
      | offset=-1  |

  Scenario: Statistics
    When alice sends GET /api/v1/kv-pairs/statistics
    Then by_status is {"pending": 2, "verified": 1}
    And by_topic is {"prayer": 2, "fasting": 1}

  Scenario: Verify or reject one pair
    When alice patches pair 1 with status "verified"
    Then the status is 200 and the status is "verified"
    When she patches it with status "maybe"
    Then the status is 422
    When she patches pair 99
    Then the status is 404

  Scenario: Verify or reject in a batch
    When alice patches /api/v1/kv-pairs with pairs 1 (rejected), 2 (verified) and 99 (verified)
    Then the answer is {"updated": 2}
    And filtering status=verified lists pairs 2 and 3
    When an item has status "bogus"
    Then the status is 422
