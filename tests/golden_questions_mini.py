"""Miniature golden-question set (ROADMAP's Phase 4 evaluation note): a regression
check for the supervisor's routing judgment specifically -- whether it correctly
decides when analysis_agent adds value versus when it would add nothing. The full
40-question accuracy/faithfulness/refusal suite is Phase 6 scope; this is just
routing, checked early rather than eyeballed until the capstone.
"""

GOLDEN_QUESTIONS = [
    {"question": "What was total revenue from delivered orders?", "expect_analysis": False},
    {"question": "How did monthly revenue trend in 2017?", "expect_analysis": True},
    {"question": "Which product categories bring in the most revenue?", "expect_analysis": True},
    {"question": "How many distinct customers have we had?", "expect_analysis": False},
    {"question": "What is the average review score?", "expect_analysis": False},
    {
        "question": "What's the average delivery time for delivered orders?",
        "expect_analysis": False,
    },
]
