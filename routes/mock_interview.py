from flask import Blueprint, request, jsonify, session
from database import read_db, write_db
from datetime import datetime
import uuid
import os
import tempfile
import subprocess
import requests
import json
import base64
import re
import traceback
import openai  # <-- NEW: OpenAI library

mock_bp = Blueprint('mock_interview', __name__)

# ========== MOCK MODE (for testing without API keys) ==========
MOCK_WHISPER = os.getenv('MOCK_WHISPER', 'false').lower() == 'true'

# ========== HELPER: OpenRouter Chat (for questions & evaluation) ==========
def call_openrouter(messages, max_tokens=500, temperature=0.7):
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        print("OPENROUTER_API_KEY not set")
        return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {
        'model': 'openai/gpt-4o-mini',
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"OpenRouter chat error: {resp.status_code} - {resp.text}")
            return None
        data = resp.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"OpenRouter call failed: {e}")
        return None

# ========== RESUME & QUESTION HELPERS ==========
def get_user_resume_content(user_id):
    db = read_db()
    resumes = [r for r in db.get('resumes', []) if r['userId'] == user_id]
    if not resumes:
        return None
    latest = sorted(resumes, key=lambda x: x.get('updatedAt', ''), reverse=True)[0]
    data = latest.get('data', {})
    parts = []
    if data.get('name'): parts.append(f"Name: {data['name']}")
    if data.get('summary'): parts.append(f"Summary: {data['summary']}")
    skills = data.get('skills')
    if skills:
        if isinstance(skills, list):
            parts.append(f"Skills: {', '.join(skills)}")
        else:
            parts.append(f"Skills: {skills}")
    if data.get('experience'): parts.append(f"Experience: {data['experience']}")
    if data.get('education'): parts.append(f"Education: {data['education']}")
    if data.get('projects'): parts.append(f"Projects: {data['projects']}")
    return "\n".join(parts)

def generate_two_questions(job_title, resume_text):
    prompt = f"""You are an interviewer. Based on the candidate's resume below and the job title "{job_title}", generate exactly 2 interview questions. One should be behavioral (e.g., "Tell me about a time..."), the other should be technical or role‑specific. Return ONLY a JSON array of two strings, e.g. ["Question 1", "Question 2"].

Resume:
{resume_text}
"""
    content = call_openrouter([{'role': 'user', 'content': prompt}], max_tokens=300, temperature=0.7)
    if not content:
        raise Exception("AI question generation failed.")
    match = re.search(r'\[\s*".*"\s*,\s*".*"\s*\]', content, re.DOTALL)
    if match:
        questions = json.loads(match.group())
        if isinstance(questions, list) and len(questions) == 2:
            return questions
    raise Exception(f"Invalid response format: {content[:200]}")

# ========== AUDIO TRANSCRIPTION - OPENAI WHISPER (RELIABLE) ==========
def transcribe_audio(audio_file_path):
    """Transcribe audio using OpenAI's direct Whisper API (fallback to mock if enabled)."""
    if MOCK_WHISPER:
        print("[Whisper] MOCK MODE: returning dummy transcript")
        return "This is a simulated transcription for testing purposes. I have relevant experience and skills for this role."

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise Exception("OPENAI_API_KEY not set. Please add your OpenAI API key to .env or enable MOCK_WHISPER=true")

    openai.api_key = api_key
    
    with open(audio_file_path, 'rb') as audio_file:
        try:
            transcript = openai.Audio.transcribe(
                model="whisper-1",
                file=audio_file
            )
            print("[Whisper] Transcription successful")
            return transcript['text']
        except Exception as e:
            print(f"[Whisper] OpenAI API error: {e}")
            raise Exception(f"Whisper transcription failed: {str(e)}")

def convert_video_to_audio(video_path):
    """Convert webm video to wav audio (16kHz mono) for Whisper."""
    audio_path = video_path.replace('.webm', '.wav')
    
    cmd = [
        'ffmpeg', '-i', video_path,
        '-acodec', 'pcm_s16le',
        '-ar', '16000',
        '-ac', '1',
        audio_path,
        '-y'
    ]
    print(f"[FFmpeg] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"[FFmpeg] Error: {result.stderr}")
        raise Exception(f"FFmpeg conversion failed: {result.stderr}")
    
    if not os.path.exists(audio_path):
        raise Exception("FFmpeg did not produce output file")
    
    return audio_path

# ========== EVALUATION ==========
def estimate_confidence(audio_path):
    """Estimate confidence based on audio duration (rough proxy)."""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            duration = float(result.stdout.strip())
            confidence = min(100, int((duration / 20.0) * 100))
            return max(0, confidence)
    except:
        pass
    return 50

def evaluate_answer(question, answer_text, job_title, resume_text):
    prompt = f"""You are an interviewer. Evaluate the candidate's answer.

Job Title: {job_title}
Resume: {resume_text}
Question: {question}
Answer: {answer_text}

Return JSON: {{"content_score": 0-100, "feedback": "short feedback", "tips": ["tip1","tip2"]}}"""
    content = call_openrouter([{'role': 'user', 'content': prompt}], max_tokens=300, temperature=0.5)
    if not content:
        raise Exception("AI evaluation failed")
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise Exception("Failed to parse evaluation response")

# ========== ROUTES ==========
@mock_bp.route('/start-session', methods=['POST'])
def start_session():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    job_title = data.get('job_title', '').strip()
    if not job_title:
        return jsonify({'error': 'Job title required'}), 400

    resume_text = get_user_resume_content(session['user_id'])
    if not resume_text:
        return jsonify({'error': 'No resume found. Please create a resume first.'}), 404

    try:
        questions = generate_two_questions(job_title, resume_text)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    db = read_db()
    session_id = str(uuid.uuid4())
    if 'mock_sessions' not in db:
        db['mock_sessions'] = []
    db['mock_sessions'].append({
        'id': session_id,
        'user_id': session['user_id'],
        'job_title': job_title,
        'questions': questions,
        'answers': [],
        'scores': [],
        'created_at': datetime.now().isoformat(),
        'status': 'in_progress'
    })
    write_db(db)
    return jsonify({'session_id': session_id, 'questions': questions}), 200

@mock_bp.route('/submit-answer', methods=['POST'])
def submit_answer():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    session_id = data.get('session_id')
    question_index = data.get('question_index')
    job_title = data.get('job_title')
    video_base64 = data.get('video', '')
    eye_contact = data.get('eye_contact', 50)
    text_answer = data.get('text_answer', '')

    temp_files = []

    try:
        if video_base64:
            # Save video
            if ',' in video_base64:
                video_base64 = video_base64.split(',')[1]
            video_data = base64.b64decode(video_base64)
            
            with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as vf:
                vf.write(video_data)
                video_path = vf.name
                temp_files.append(video_path)
            
            # Convert to audio
            audio_path = convert_video_to_audio(video_path)
            temp_files.append(audio_path)
            
            # Transcribe
            transcript = transcribe_audio(audio_path)
            confidence = estimate_confidence(audio_path)
        else:
            # Text mode
            transcript = text_answer.strip()
            if not transcript:
                return jsonify({'error': 'No answer provided'}), 400
            confidence = 50
            eye_contact = 50

        # Load session and evaluate
        db = read_db()
        sessions_list = db.get('mock_sessions', [])
        sess = next((s for s in sessions_list if s['id'] == session_id and s['user_id'] == session['user_id']), None)
        if not sess:
            return jsonify({'error': 'Session not found'}), 404

        question = sess['questions'][question_index]
        resume_text = get_user_resume_content(session['user_id'])
        
        evaluation = evaluate_answer(question, transcript, job_title, resume_text)
        content_score = evaluation.get('content_score', 50)
        final_score = int(content_score * 0.6 + confidence * 0.2 + eye_contact * 0.2)

        sess['answers'].append({
            'question': question,
            'answer': transcript,
            'score': final_score,
            'feedback': evaluation.get('feedback', 'No feedback'),
            'tips': evaluation.get('tips', ['Practice more'])
        })
        sess['scores'].append(final_score)
        write_db(db)

        return jsonify({
            'score': final_score,
            'feedback': evaluation.get('feedback', 'Good effort'),
            'tips': evaluation.get('tips', ['Keep practicing']),
            'transcript': transcript
        }), 200

    except Exception as e:
        print(f"[ERROR] {traceback.format_exc()}")
        return jsonify({'error': f'Internal error: {str(e)}'}), 500

    finally:
        for f in temp_files:
            try:
                if os.path.exists(f):
                    os.unlink(f)
            except:
                pass

@mock_bp.route('/end-session', methods=['POST'])
def end_session():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    session_id = data.get('session_id')
    db = read_db()
    sessions_list = db.get('mock_sessions', [])
    for s in sessions_list:
        if s['id'] == session_id and s['user_id'] == session['user_id']:
            s['status'] = 'completed'
            s['ended_at'] = datetime.now().isoformat()
            write_db(db)
            return jsonify({'message': 'Session saved'}), 200
    return jsonify({'error': 'Session not found'}), 404