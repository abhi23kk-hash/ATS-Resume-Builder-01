from flask import Blueprint, request, jsonify, session
from database import read_db, write_db
import uuid
from datetime import datetime

resources_bp = Blueprint('resources', __name__)

# ========== GET all resume examples (public, no login required) ==========
@resources_bp.route('/resume-examples', methods=['GET'])
def get_resume_examples():
    db = read_db()
    examples = db.get('resume_examples', [])
    # sort by created_at desc
    examples.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify(examples), 200

# ========== ADD a new resume example (admin only – you can call this via POST) ==========
@resources_bp.route('/resume-examples', methods=['POST'])
def add_resume_example():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    # Optional: check if user is admin (you can add a role field later)
    data = request.json
    required = ['title', 'industry', 'image_url', 'description']
    for field in required:
        if field not in data:
            return jsonify({'error': f'{field} is required'}), 400
    new_example = {
        'id': str(uuid.uuid4()),
        'title': data['title'],
        'industry': data['industry'],
        'image_url': data['image_url'],
        'description': data.get('description', ''),
        'created_at': datetime.now().isoformat()
    }
    db = read_db()
    if 'resume_examples' not in db:
        db['resume_examples'] = []
    db['resume_examples'].append(new_example)
    write_db(db)
    return jsonify(new_example), 201

# ========== DELETE a resume example (optional) ==========
@resources_bp.route('/resume-examples/<example_id>', methods=['DELETE'])
def delete_resume_example(example_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    db = read_db()
    examples = db.get('resume_examples', [])
    new_examples = [e for e in examples if e['id'] != example_id]
    if len(new_examples) == len(examples):
        return jsonify({'error': 'Not found'}), 404
    db['resume_examples'] = new_examples
    write_db(db)
    return jsonify({'message': 'Deleted'}), 200
