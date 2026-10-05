# Verified against: backend/services/exact_text.py, backend/services/ranking.py,
# backend/services/suggestions.py, backend/routers/suggestions.py, tests/test_exact_search.py,
# tests/test_suggestions.py, tests/security/test_suggestions.py
Feature: Exact keyword search, suggestions and typo hints
  The "exact" method finds hadiths that contain every typed word as a whole word. It does not
  stem or lemmatize, so "pray" does not find "prayer". GET /api/v1/suggestions completes a
  partial word from the vocabulary and the chapter titles. When a keyword search finds nothing,
  the answer may carry a "did_you_mean" hint. Nobody needs a token.

  Background:
    Given the app runs with the search feature on
    And the corpus, the BM25 index and the embeddings are loaded

  Scenario: Every word must be present
    When a client searches "prayer fasting" with method "exact"
    Then the hadith ids returned are 3
    When a client searches "prayer" with method "exact"
    Then the hadith ids returned are 1, 3

  Scenario: Words are not stemmed
    When a client searches "pray" with method "exact"
    Then number_of_results is 0

  Scenario: Case and Arabic diacritics do not matter
    When a client searches "PRAYER" with method "exact"
    Then the hadith ids returned are 1, 3
    When a client searches "الصَّلاة" with method "exact" and lang "ar"
    Then the hadith ids returned are 1, 3

  Scenario: The fused method answers Arabic only
    When a client searches "prayer" with method "exact-semantic-rrf" and lang "en"
    Then the status is 422
    When a client searches "الصلاة" with method "exact-semantic-rrf" and lang "ar"
    Then the status is 200
    And the first hadith id is 1

  Scenario: A typo in a keyword search gets a hint
    When a client searches "prayr" with method "exact"
    Then number_of_results is 0
    And did_you_mean is "prayer"

  Scenario: A normal answer has no hint
    When a client searches "prayer" with method "exact"
    Then the body has no did_you_mean field

  Scenario: Suggestions complete a partial word
    When a client sends GET /api/v1/suggestions with q "pra"
    Then the status is 200
    And the first suggestion is the term "prayer"
    And _links.self.href is "/api/v1/suggestions?q=pra"

  Scenario Outline: Bad suggestion requests are refused with a problem+json 422
    When a client sends GET /api/v1/suggestions with <params>
    Then the status is 422
    And the content type is application/problem+json

    Examples:
      | params          |
      | no q            |
      | q empty         |
      | q 101 letters   |
      | q=a, limit=0    |
      | q=a, limit=11   |
