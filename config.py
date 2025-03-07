"""Configuration settings for the Quiz Assistant.

This module contains all configurable parameters for the Quiz Assistant application,
including the prompt template, generation settings, and model selection.
"""

# Prompt template for Gemini model
# This is the system prompt that guides how Gemini interprets and responds to images
# Modify this to change the assistant's behavior, response format, and rules
GEMINI_PROMPT = """You are a knowledgeable assistant. Analyze the image and:
1. If it's a multiple choice question, respond ONLY with the correct answer (e.g., 'APPLE')
2. If it's any other type of question, provide the shortest possible accurate answer
3. If it's a statement or information, summarize the key point in 3-5 words

Rules:
- Never explain your reasoning
- Never repeat the question
- Keep answers extremely concise
- If it's a calculation, just show the final number
- If it's a date, just show the date
- If it's a name, just show the name"""

# Generation configuration for Gemini model
# These parameters control the model's response generation behavior
GENERATION_CONFIG = {
    # Temperature (0.0 - 1.0): Controls randomness in the response
    # - Lower values (0.0 - 0.3): More focused, consistent, and deterministic responses
    # - Higher values (0.7 - 1.0): More creative, diverse, but potentially less reliable responses
    # - Default: 0.1 for high accuracy in quiz answers
    "temperature": 0.1,

    # Candidate count: Number of response options to generate
    # - Higher values generate more alternatives but increase API usage
    # - Default: 1 since we need a single definitive answer
    "candidate_count": 1,

    # Max output tokens: Maximum length of the generated response
    # - Lower values ensure concise answers
    # - 20 tokens ≈ 15-20 words
    # - Increase if you need longer explanations
    "max_output_tokens": 20,
}

# Model configuration
# Available models: # Check Google AI Studio for up-to-date models
# - 'gemini-2.0-flash': Fastest, optimized for quick responses
# - 'gemini-2.0-pro': More capable but slightly slower
# - 'gemini-2.0-pro-exp-02-05': Experimental version with potential improvements
MODEL_NAME = 'gemini-2.0-flash'

# Example modifications for different use cases:
"""
# For more detailed explanations:
GENERATION_CONFIG = {
    "temperature": 0.3,
    "candidate_count": 1,
    "max_output_tokens": 50,
}

# For creative responses:
GENERATION_CONFIG = {
    "temperature": 0.7,
    "candidate_count": 1,
    "max_output_tokens": 30,
}

# For multiple answer suggestions:
GENERATION_CONFIG = {
    "temperature": 0.5,
    "candidate_count": 3,
    "max_output_tokens": 20,
}
""" 