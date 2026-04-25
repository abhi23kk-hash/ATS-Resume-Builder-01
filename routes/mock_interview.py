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
import whisper
from collections import defaultdict

mock_bp = Blueprint('mock_interview', __name__)

# ========== TRACK ASKED QUESTIONS PER USER ==========
asked_questions = defaultdict(list)

# ========== MOCK MODE (for testing without transcription) ==========
MOCK_WHISPER = os.getenv('MOCK_WHISPER', 'false').lower() == 'true'

# ========== HELPER: OpenRouter Chat ==========
def call_openrouter(messages, max_tokens=500, temperature=0.9):
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        print("❌ OPENROUTER_API_KEY not set")
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
            print(f"❌ OpenRouter chat error: {resp.status_code} - {resp.text[:200]}")
            return None
        data = resp.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"❌ OpenRouter call failed: {e}")
        return None

# ========== RESUME VALIDATION ENDPOINT ==========
@mock_bp.route('/validate-resume', methods=['POST'])
def validate_resume():
    """AI-powered resume validation - checks if pasted text is a valid resume"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    resume_text = data.get('resume_text', '')
    
    if not resume_text:
        return jsonify({
            'is_valid': False,
            'message': 'Please paste your resume content.'
        }), 200
    
    if len(resume_text) < 100:
        return jsonify({
            'is_valid': False,
            'message': f'Resume text is too short ({len(resume_text)} characters). A valid resume should have at least 100 characters.'
        }), 200
    
    # Quick keyword-based validation first
    text_lower = resume_text.lower()
    has_experience = any(w in text_lower for w in ['experience', 'work', 'employment', 'job'])
    has_skills = any(w in text_lower for w in ['skill', 'technologies', 'technical'])
    has_education = any(w in text_lower for w in ['education', 'degree', 'university', 'college', 'bachelor', 'master'])
    has_projects = 'project' in text_lower
    
    keyword_score = sum([has_experience, has_skills, has_education, has_projects])
    
    if keyword_score < 2:
        missing = []
        if not has_experience: missing.append('Experience/Work history')
        if not has_skills: missing.append('Skills')
        if not has_education: missing.append('Education')
        if not has_projects: missing.append('Projects')
        
        return jsonify({
            'is_valid': False,
            'message': f'Missing sections: {", ".join(missing)}. Please add these to your resume.',
            'detected_sections': ', '.join([s for s in ['Experience', 'Skills', 'Education', 'Projects'] if locals().get(f'has_{s.lower().split("/")[0]}', False)])
        }), 200
    
    # Try AI-based validation for more accuracy
    prompt = f"""You are a resume validator. Analyze the following text and determine if it is a valid professional resume.

A valid resume MUST contain:
- Professional experience or work history
- Skills section
- Education or qualifications
- Professional language and structure

Text to analyze:
{resume_text[:2000]}

Return ONLY a JSON object with this exact format:
{{"is_valid": true/false, "message": "brief explanation (max 100 chars)", "detected_sections": "comma separated list of sections found"}}"""

    content = call_openrouter([{'role': 'user', 'content': prompt}], max_tokens=200, temperature=0.3)
    
    if content:
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                detected = result.get('detected_sections', 'Experience, Skills, Education')
                return jsonify({
                    'is_valid': result.get('is_valid', False),
                    'message': result.get('message', 'Resume validation complete'),
                    'detected_sections': detected
                }), 200
            except:
                pass
    
    # Fallback validation (if AI fails)
    detected = []
    if has_experience: detected.append('Experience')
    if has_skills: detected.append('Skills')
    if has_education: detected.append('Education')
    if has_projects: detected.append('Projects')
    
    return jsonify({
        'is_valid': True,
        'message': '✅ Resume validated successfully!',
        'detected_sections': ', '.join(detected)
    }), 200

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

def generate_fallback_questions(resume_text):
    """Generate questions based ONLY on resume content (no job title)"""
    skills = []
    if "Skills:" in resume_text:
        skills_section = resume_text.split("Skills:")[1].split("\n")[0]
        skills = [s.strip() for s in skills_section.split(",")]
    
    projects = []
    if "Projects:" in resume_text:
        projects_section = resume_text.split("Projects:")[1].split("\n")[0]
        projects = [p.strip() for p in projects_section.split(",")]
    
    if skills:
        q1 = f"Based on your resume, you have experience with {skills[0]}. Can you describe a specific project where you used this skill?"
    elif projects:
        q1 = f"Tell me about your project '{projects[0]}'. What was your specific contribution?"
    else:
        q1 = "What technical skills from your resume are you most proud of and why?"
    
    if projects and len(projects) > 0:
        q2 = f"Walk me through your project '{projects[0]}'. What challenges did you face and how did you overcome them?"
    elif skills and len(skills) > 1:
        q2 = f"How do you integrate {skills[0]} with {skills[1]} in your workflow?"
    else:
        q2 = "What excites you most about your field based on your resume experience?"
    
    return [q1, q2]

def generate_two_questions(resume_text):
    """Generate questions based ONLY on resume (no job title)"""
    user_id = session.get('user_id', 'default')
    prev_questions = asked_questions.get(user_id, [])
    prev_questions_text = "\n".join([f"- {q}" for q in prev_questions[-4:]]) if prev_questions else "None yet"
    
    prompt = f"""You are an expert technical interviewer. Based STRICTLY on the candidate's resume below, generate 2 UNIQUE interview questions.

CRITICAL RULES:
- DO NOT ask generic questions like "Tell me about yourself"
- DO NOT use any job title - base questions ONLY on the resume content
- Questions MUST reference SPECIFIC skills, projects, or experiences from the resume
- DO NOT repeat these previously asked questions:
{prev_questions_text}

Resume:
{resume_text}

Return ONLY a JSON array of two strings. Example format: ["Question about specific skill from resume?", "Question about specific project from resume?"]"""

    content = call_openrouter([{'role': 'user', 'content': prompt}], max_tokens=400, temperature=0.9)
    
    if content:
        match = re.search(r'\[\s*".*"\s*,\s*".*"\s*\]', content, re.DOTALL)
        if match:
            try:
                questions = json.loads(match.group())
                if isinstance(questions, list) and len(questions) == 2:
                    asked_questions[user_id].extend(questions)
                    return questions
            except:
                pass
    
    print("⚠️ Using fallback questions")
    return generate_fallback_questions(resume_text)

# ========== AUDIO TRANSCRIPTION - LOCAL WHISPER ==========
def transcribe_audio(audio_file_path):
    if MOCK_WHISPER:
        return "This is a simulated transcription for testing purposes. I have relevant experience and skills for this role."
    
    try:
        print("🎤 Loading Whisper model...")
        model = whisper.load_model("base")
        print(f"🎤 Transcribing audio file: {audio_file_path}")
        result = model.transcribe(audio_file_path, language="en")
        transcript = result["text"].strip()
        if not transcript:
            raise Exception("No speech detected in audio")
        print(f"✅ Transcription successful: {transcript[:100]}...")
        return transcript
    except Exception as e:
        print(f"❌ Local Whisper error: {e}")
        raise Exception(f"Transcription failed: {str(e)}")

def convert_video_to_audio(video_path):
    audio_path = video_path.replace('.webm', '.wav')
    cmd = ['ffmpeg', '-i', video_path, '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_path, '-y']
    print(f"🔄 Converting video to audio...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"❌ FFmpeg error: {result.stderr}")
        raise Exception(f"FFmpeg conversion failed: {result.stderr}")
    return audio_path

def estimate_confidence(audio_path):
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.stdout.strip():
            duration = float(result.stdout.strip())
            return min(100, int((duration / 20.0) * 100))
    except:
        pass
    return 50

def evaluate_answer(question, answer_text, resume_text):
    prompt = f"""Evaluate this interview answer based ONLY on the resume.

Resume: {resume_text}
Question: {question}
Answer: {answer_text}

Return JSON: {{"score": 0-100, "feedback": "short feedback (2-3 sentences)", "tips": ["tip1","tip2"]}}"""
    
    content = call_openrouter([{'role': 'user', 'content': prompt}], max_tokens=300, temperature=0.5)
    
    if content:
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
    
    # Fallback
    return {
        "score": 70,
        "feedback": "Good attempt. Keep practicing to improve your answers.",
        "tips": ["Be more specific with examples from your resume", "Use the STAR method", "Practice regularly"]
    }

# ========== ROUTES ==========

@mock_bp.route('/start-session', methods=['POST'])
def start_session():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    resume_text = data.get('resume_text', '')
    
    if not resume_text:
        resume_text = get_user_resume_content(session['user_id'])
    
    if not resume_text:
        return jsonify({'error': 'No resume found. Please paste your resume first.'}), 404

    try:
        questions = generate_two_questions(resume_text)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    db = read_db()
    session_id = str(uuid.uuid4())
    if 'mock_sessions' not in db:
        db['mock_sessions'] = []
    db['mock_sessions'].append({
        'id': session_id,
        'user_id': session['user_id'],
        'resume_text': resume_text,
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
            transcript = text_answer.strip()
            if not transcript:
                return jsonify({'error': 'No answer provided'}), 400
            confidence = 50
            eye_contact = 50
        
        # Load session
        db = read_db()
        sessions_list = db.get('mock_sessions', [])
        sess = next((s for s in sessions_list if s['id'] == session_id and s['user_id'] == session['user_id']), None)
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        # Evaluate
        question = sess['questions'][question_index]
        resume_text = sess.get('resume_text', '')
        
        evaluation = evaluate_answer(question, transcript, resume_text)
        content_score = evaluation.get('score', 50)
        final_score = int(content_score * 0.6 + confidence * 0.2 + eye_contact * 0.2)
        
        # Save answer
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
        print(f"❌ ERROR: {traceback.format_exc()}")
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