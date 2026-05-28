"""
BlazeMarket - SMM Services Bot & Web App
Flask web server for Web App
"""
import os
import json
from flask import Flask, render_template, request, jsonify, send_from_directory
from config import cfg
from database import db

app = Flask(__name__, 
            template_folder='templates',
            static_folder='static')


@app.route('/')
def index():
    """Main Web App page"""
    return render_template('index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files"""
    return send_from_directory('static', filename)


@app.route('/api/services', methods=['GET'])
def get_services():
    """Get services by category"""
    category = request.args.get('category', 'telegram')
    provider = request.args.get('provider', 'twiboost')
    
    services = db.get_services_by_category(category, provider)
    
    return jsonify({
        'success': True,
        'services': [dict(s) for s in services]
    })


@app.route('/api/services/all', methods=['GET'])
def get_all_services():
    """Get all services"""
    provider = request.args.get('provider', 'twiboost')
    
    services = db.get_all_services(provider)
    
    return jsonify({
        'success': True,
        'services': [dict(s) for s in services]
    })


@app.route('/api/order/code', methods=['POST'])
def create_order_code():
    """Create order code for comments"""
    data = request.json
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    service_type = data.get('service_type', 'comments')
    
    # Generate code
    import secrets
    import string
    code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    
    # Store in database
    db.create_order_code(code, chat_id, message_id, service_type)
    
    return jsonify({
        'success': True,
        'code': code
    })


@app.route('/api/order/submit', methods=['POST'])
def submit_order():
    """Submit order with comments"""
    data = request.json
    code = data.get('code')
    comments = data.get('comments')
    comments_file = data.get('comments_file')
    user_id = data.get('user_id')
    
    # Validate code
    order_code = db.get_order_code(code)
    if not order_code:
        return jsonify({
            'success': False,
            'error': 'Invalid code'
        }), 400
    
    # Update order code status
    db.update_order_code_status(code, 'submitted')
    
    return jsonify({
        'success': True,
        'message': 'Order submitted successfully'
    })


@app.route('/api/mirror/create', methods=['POST'])
def create_mirror():
    """Create mirror shop"""
    data = request.json
    owner_id = data.get('owner_id')
    name = data.get('name', f'Shop_{owner_id}')
    
    # Generate code
    import secrets
    import string
    code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    
    # Create mirror
    mirror_id = db.create_mirror(owner_id, name, code)
    
    return jsonify({
        'success': True,
        'mirror_id': mirror_id,
        'code': code
    })


@app.route('/api/mirror/<code>', methods=['GET'])
def get_mirror(code):
    """Get mirror info"""
    mirror = db.get_mirror_by_code(code)
    
    if not mirror:
        return jsonify({
            'success': False,
            'error': 'Mirror not found'
        }), 404
    
    return jsonify({
        'success': True,
        'mirror': dict(mirror)
    })


@app.route('/api/payment/pally', methods=['POST'])
def create_pally_payment():
    """Create Pally payment link"""
    data = request.json
    amount = data.get('amount', 0)
    user_id = data.get('user_id')
    description = data.get('description', '')
    
    merchant_id = cfg.pally_merchant_id
    secret_key = cfg.pally_secret_key
    
    if not merchant_id or not secret_key:
        return jsonify({
            'success': False,
            'error': 'Payment not configured'
        }), 500
    
    # Generate order ID
    import hashlib
    from datetime import datetime
    order_id = f"BM_{user_id}_{int(datetime.now().timestamp())}"
    
    # Create signature
    sign_string = f"{merchant_id}:{amount}:{order_id}:{secret_key}"
    signature = hashlib.sha256(sign_string.encode()).hexdigest()
    
    # Build payment URL
    params = {
        'merchant_id': merchant_id,
        'amount': amount,
        'order_id': order_id,
        'description': description,
        'signature': signature
    }
    
    payment_url = "https://pally.info/merchant/pay?" + "&".join(f"{k}={v}" for k, v in params.items())
    
    return jsonify({
        'success': True,
        'payment_url': payment_url,
        'order_id': order_id
    })


@app.route('/api/admin/stats', methods=['GET'])
def get_admin_stats():
    """Get admin statistics"""
    # Implement stats logic
    return jsonify({
        'success': True,
        'stats': {
            'total_users': 0,
            'total_orders': 0,
            'total_revenue': 0,
            'active_mirrors': 0
        }
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
