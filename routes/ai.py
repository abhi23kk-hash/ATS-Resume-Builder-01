from flask import Blueprint, request, jsonify, session
import os
import random
import requests
import json
from dotenv import load_dotenv

load_dotenv()

ai_bp = Blueprint('ai', __name__)

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_BASE_URL = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
HAS_AI = bool(OPENROUTER_API_KEY and OPENROUTER_API_KEY not in ('', 'your_key_here'))

def is_authenticated():
    return 'user_id' in session

# ---------- Fallback functions (used when no API key) ----------
def fallback_ats_score(resume_text, job_desc):
    words = (resume_text + ' ' + job_desc).lower()
    keyword_pool = ['experience', 'project', 'team', 'lead', 'python', 'node', 'aws', 'sql', 'cloud', 'javascript', 'react', 'api', 'development']
    found = [kw for kw in keyword_pool if kw in words]
    score = max(20, min(80, round(len(found) / len(keyword_pool) * 80)))
    missing = [kw for kw in keyword_pool if kw not in found]
    return {
        'score': score,
        'missingKeywords': missing[:8],
        'strengths': ['Basic structure found', 'Contact information present'],
        'weaknesses': ['Limited ATS keywords', 'Add measurable achievements'],
        'suggestions': ['Use action verbs', 'Add quantifiable results', 'Match job description keywords']
    }

def fallback_chat(user_message, resume_content):
    responses = [
        "Your resume looks promising. Consider adding more quantifiable achievements.",
        "For interview prep, practice the STAR method (Situation, Task, Action, Result).",
        "To improve ATS score, include keywords from the job description."
    ]
    return random.choice(responses) + "\n\n(Note: AI service is in fallback mode. Add your OpenRouter API key for full functionality.)"

# ---------- Real AI call (synchronous, using requests) ----------
def call_openrouter(messages):
    """Send messages to OpenRouter and return the assistant's reply."""
    if not HAS_AI:
        return None
    try:
        headers = {
            'Authorization': f'Bearer {OPENROUTER_API_KEY}',
            'Content-Type': 'application/json'
        }
        payload = {
            'model': 'openai/gpt-4o-mini',
            'messages': messages,
            'max_tokens': 500,
            'temperature': 0.7
        }
        response = requests.post(f'{OPENROUTER_BASE_URL}/chat/completions', json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            print(f"OpenRouter error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print("OpenRouter call failed:", e)
        return None

# ---------- ATS Score Endpoint ----------
@ai_bp.route('/ats-score', methods=['POST'])
def ats_score():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    resume_content = data.get('resumeContent', '')
    job_description = data.get('jobDescription', '')

    if not resume_content or not job_description:
        return jsonify({'error': 'Resume content and job description are required'}), 400

    if not HAS_AI:
        result = fallback_ats_score(resume_content, job_description)
        return jsonify(result), 200

    prompt = f"""Analyze this resume against the following job description. 
Return ONLY a JSON object with this structure:
{{"score": number (0-100), "missingKeywords": ["string"], "strengths": ["string"], "weaknesses": ["string"], "suggestions": ["string"]}}

Resume: {resume_content}

Job Description: {job_description}"""

    messages = [
        {"role": "system", "content": "You are a professional ATS optimizer. Return valid JSON only."},
        {"role": "user", "content": prompt}
    ]
    response = call_openrouter(messages)
    if response:
        try:
            result = json.loads(response)
            return jsonify(result), 200
        except:
            pass
    return jsonify(fallback_ats_score(resume_content, job_description)), 200

# ---------- Chatbot Endpoint (with personality & conversation history) ----------
@ai_bp.route('/chat', methods=['POST'])
def chat():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    user_message = data.get('userMessage', '').strip()
    resume_content = data.get('resumeContent', '')
    system_prompt = data.get('systemPrompt', 
        "You are a professional career assistant and resume expert. Keep answers concise, practical, and helpful. You remember previous messages in this conversation.")
    conversation_history = data.get('conversationHistory', [])  # list of {role, text} from frontend

    if not user_message:
        return jsonify({'error': 'Message required'}), 400

    # Build messages for AI
    messages = [{"role": "system", "content": system_prompt}]
    
    # Add resume context only if provided and not already in history (simple heuristic)
    if resume_content and not any(msg.get('role') == 'system' and 'resume content' in msg.get('content', '').lower() for msg in conversation_history):
        messages.append({"role": "system", "content": f"User's resume content for reference: {resume_content[:2000]}"})
    
    # Add conversation history (limit to last 15 to avoid token overflow)
    for msg in conversation_history[-15:]:
        # Ensure each message has role and content
        role = msg.get('role')
        content = msg.get('text') or msg.get('content')
        if role and content:
            messages.append({"role": role, "content": content})
    
    # Add current user message
    messages.append({"role": "user", "content": user_message})

    if not HAS_AI:
        reply = fallback_chat(user_message, resume_content)
        return jsonify({'response': reply}), 200

    response = call_openrouter(messages)
    if response:
        reply = response
    else:
        reply = fallback_chat(user_message, resume_content)

    return jsonify({'response': reply}), 200

# ---------- Optional: Clear session chat history (for old session-based clients) ----------
@ai_bp.route('/clear-history', methods=['POST'])
def clear_history():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    session.pop('chat_history', None)
    return jsonify({'message': 'Chat history cleared'}), 200

# ---------- Health Check ----------
@ai_bp.route('/status', methods=['GET'])
def status():
    return jsonify({
        'ai_configured': HAS_AI,
        'provider': 'OpenRouter' if HAS_AI else 'None'
    }), 200