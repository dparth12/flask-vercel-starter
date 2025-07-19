from flask import Flask, request, jsonify, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import requests
import time
import os
import logging
from functools import wraps
import threading
import json
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 300,
    'pool_pre_ping': True,
    'pool_size': 10,
    'max_overflow': 20
}

db = SQLAlchemy(app)

# Global token info
token_info = {
    "access_token": None,
    "expiry_time": None,
    "lock": threading.Lock()
}

# FatSecret OAuth credentials
CLIENT_ID = os.environ.get('FATSECRET_CLIENT_ID')
CLIENT_SECRET = os.environ.get('FATSECRET_CLIENT_SECRET')

# Database Models
class UserDate(db.Model):
    __tablename__ = 'user_dates'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(255), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    meals = db.Column(db.JSON)
    notes = db.Column(db.Text)
    totals = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'date', name='unique_user_date'),
        db.Index('idx_user_date', 'user_id', 'date'),
    )

class FoodCache(db.Model):
    __tablename__ = 'food_cache'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    food_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    food_data = db.Column(db.JSON)
    serving_sizes = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FoodItemLegacy(db.Model):
    __tablename__ = 'food_items_legacy'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(255), nullable=False, index=True)
    meal_id = db.Column(db.BigInteger, nullable=False, index=True)
    food_id = db.Column(db.String(255), nullable=False)
    serving_id = db.Column(db.String(255), nullable=False)
    servings = db.Column(db.Numeric(10, 2), default=1)
    
    # Full nutrition data stored
    foodname = db.Column(db.String(500))
    brandname = db.Column(db.String(500))
    calories = db.Column(db.Numeric(10, 2), default=0)
    carbs = db.Column(db.Numeric(10, 2), default=0)
    protein = db.Column(db.Numeric(10, 2), default=0)
    fats = db.Column(db.Numeric(10, 2), default=0)
    saturated_fat = db.Column(db.Numeric(10, 2), default=0)
    cholesterol = db.Column(db.Numeric(10, 2), default=0)
    sodium = db.Column(db.Numeric(10, 2), default=0)
    dietary_fiber = db.Column(db.Numeric(10, 2), default=0)
    sugar = db.Column(db.Numeric(10, 2), default=0)
    potassium = db.Column(db.Numeric(10, 2), default=0)
    trans_fat = db.Column(db.Numeric(10, 2), default=0)
    mono_fat = db.Column(db.Numeric(10, 2), default=0)
    poly_fat = db.Column(db.Numeric(10, 2), default=0)
    vit_a = db.Column(db.Numeric(10, 2), default=0)
    vit_c = db.Column(db.Numeric(10, 2), default=0)
    vit_d = db.Column(db.Numeric(10, 2), default=0)
    net_carbs = db.Column(db.Numeric(10, 2), default=0)
    sugar_alc = db.Column(db.Numeric(10, 2), default=0)
    sugar_added = db.Column(db.Numeric(10, 2), default=0)
    iron = db.Column(db.Numeric(10, 2), default=0)
    calcium = db.Column(db.Numeric(10, 2), default=0)
    serving_qty = db.Column(db.Numeric(10, 2), default=1)
    serving_unit = db.Column(db.String(255))
    nutrition_multiplier = db.Column(db.Numeric(10, 2), default=1.0)
    verified = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.Index('idx_legacy_user_meal', 'user_id', 'meal_id'),
    )

class FoodItemCompliant(db.Model):
    __tablename__ = 'food_items_compliant'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(255), nullable=False, index=True)
    meal_id = db.Column(db.BigInteger, nullable=False, index=True)
    food_id = db.Column(db.String(255), nullable=False)
    serving_id = db.Column(db.String(255), nullable=False)
    servings = db.Column(db.Numeric(10, 2), default=1)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.Index('idx_compliant_user_meal', 'user_id', 'meal_id'),
    )

# Token management
def get_token(force_refresh=False):
    """Get access token with thread-safe caching"""
    with token_info["lock"]:
        # Check if we have a valid token
        if not force_refresh and token_info["access_token"]:
            if token_info["expiry_time"] and time.time() < token_info["expiry_time"] - 60:
                return token_info["access_token"]
        
        # Get new token
        logger.info("🔄 Requesting new FatSecret access token...")
        
        auth_url = "https://oauth.fatsecret.com/connect/token"
        auth_data = {
            "grant_type": "client_credentials",
            "scope": "premier"
        }
        
        try:
            response = requests.post(
                auth_url,
                data=auth_data,
                auth=(CLIENT_ID, CLIENT_SECRET),
                timeout=10
            )
            response.raise_for_status()
            
            token_data = response.json()
            token_info["access_token"] = token_data["access_token"]
            token_info["expiry_time"] = time.time() + token_data["expires_in"]
            
            logger.info("✅ New access token obtained successfully")
            return token_info["access_token"]
            
        except Exception as e:
            logger.error(f"❌ Failed to get access token: {str(e)}")
            raise Exception(f"Failed to obtain access token: {str(e)}")

def token_required(f):
    """Decorator to ensure valid token before API calls"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            token = get_token()
            return f(token, *args, **kwargs)
        except Exception as e:
            logger.error(f"Token error in {f.__name__}: {str(e)}")
            return jsonify({"error": "Authentication failed"}), 401
    return decorated_function

def call_api(url, headers, data=None, method="POST", max_retries=3):
    """Make API call with retry logic"""
    for attempt in range(max_retries):
        try:
            if method.upper() == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=30)
            else:
                response = requests.get(url, headers=headers, params=data, timeout=30)
            
            # Check for token errors and retry with fresh token
            if response.status_code == 401 or (response.status_code == 200 and '"error"' in response.text):
                if attempt < max_retries - 1:
                    logger.warning(f"Token error on attempt {attempt + 1}, refreshing token...")
                    headers["Authorization"] = f"Bearer {get_token(force_refresh=True)}"
                    continue
            
            response.raise_for_status()
            return response
            
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise e
            logger.warning(f"Request failed on attempt {attempt + 1}: {str(e)}")
            time.sleep(2 ** attempt)
    
    raise Exception(f"Failed after {max_retries} attempts")

# FatSecret API routes (existing)
@app.route("/api/foods/search", methods=["GET"])
@token_required
def search_foods(token):
    """Search for foods by name"""
    search_expression = request.args.get("search_expression", "")
    page_number = request.args.get("page_number", "0")
    max_results = request.args.get("max_results", "20")
    
    if not search_expression:
        return jsonify({"error": "Missing required parameter: search_expression"}), 400
    
    try:
        api_url = "https://platform.fatsecret.com/rest/server.api"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {token}"
        }
        
        params = {
            "method": "foods.search",
            "search_expression": search_expression,
            "page_number": page_number,
            "max_results": max_results,
            "format": "json"
        }
        
        response = call_api(api_url, headers, params, method="POST")
        return jsonify(response.json())
        
    except Exception as e:
        logger.error(f"Error in search_foods: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/food/get", methods=["GET"])
@token_required
def get_food(token):
    """Get detailed information about a specific food by ID"""
    food_id = request.args.get("food_id", "")
    
    if not food_id:
        return jsonify({"error": "Missing required parameter: food_id"}), 400
    
    try:
        # Check cache first
        cached_food = FoodCache.query.filter_by(food_id=food_id).first()
        if cached_food and cached_food.food_data:
            cache_age = datetime.utcnow() - cached_food.updated_at
            if cache_age.days < 7:  # Use cache for 7 days
                logger.info(f"Using cached data for food_id: {food_id}")
                return jsonify(cached_food.food_data)
        
        api_url = "https://platform.fatsecret.com/rest/server.api"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {token}"
        }
        
        params = {
            "method": "food.get",
            "food_id": food_id,
            "format": "json"
        }
        
        response = call_api(api_url, headers, params, method="POST")
        food_data = response.json()
        
        # Update cache
        if cached_food:
            cached_food.food_data = food_data
            cached_food.updated_at = datetime.utcnow()
        else:
            cached_food = FoodCache(
                food_id=food_id,
                food_data=food_data,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(cached_food)
        
        db.session.commit()
        return jsonify(food_data)
        
    except Exception as e:
        logger.error(f"Error in get_food: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/food/barcode", methods=["GET"])
@token_required
def find_food_by_barcode(token):
    """Find food information using a barcode"""
    barcode = request.args.get("barcode", "")
    region = request.args.get("region", "")
    language = request.args.get("language", "")
    
    if not barcode:
        return jsonify({"error": "Missing required parameter: barcode"}), 400
    
    try:
        # Convert UPC-E to UPC-A if needed
        if len(barcode) == 8 and barcode.isdigit():
            try:
                barcode = upce_to_upca(barcode)
                logger.info(f"Converted UPC-E to UPC-A: {barcode}")
            except ValueError as e:
                logger.warning(f"Failed to convert UPC-E: {str(e)}")
        
        api_url = "https://platform.fatsecret.com/rest/server.api"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {token}"
        }
        
        params = {
            "method": "food.find_id_for_barcode",
            "barcode": barcode,
            "format": "json"
        }
        
        if region:
            params["region"] = region
            if language:
                params["language"] = language
        
        response = call_api(api_url, headers, params, method="POST")
        return jsonify(response.json())
        
    except Exception as e:
        logger.error(f"Error in find_food_by_barcode: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Nutrition tracking routes
@app.route('/api/nutrition/user/<user_id>', methods=['GET', 'POST'])
def handle_user_profile(user_id):
    """Handle user profile operations"""
    try:
        if request.method == 'GET':
            # For now, return basic user info
            return jsonify({
                'success': True,
                'user': {
                    'id': user_id,
                    'created_at': datetime.utcnow().isoformat()
                }
            })
        
        elif request.method == 'POST':
            # Handle user profile updates
            data = request.json or {}
            return jsonify({
                'success': True,
                'message': 'User profile updated',
                'user_id': user_id
            })
    
    except Exception as e:
        logger.error(f"Error in handle_user_profile: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nutrition/user/<user_id>/date/<date>', methods=['GET', 'POST'])
def handle_date_details(user_id, date):
    """Handle date-specific nutrition data"""
    try:
        # Parse date
        try:
            date_obj = datetime.strptime(date, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        if request.method == 'GET':
            logger.info(f"🔄 Fetching date details for {date}, user: {user_id}")
            
            # Get existing date record
            date_record = UserDate.query.filter_by(user_id=user_id, date=date_obj).first()
            
            if not date_record:
                # Create new date record with default meals
                default_meals = [
                    {
                        "id": int(f"{int(time.time())}{i}"),
                        "name": meal_name,
                        "food_items": []
                    }
                    for i, meal_name in enumerate(["Breakfast", "Lunch", "Dinner"], 1)
                ]
                
                date_record = UserDate(
                    user_id=user_id,
                    date=date_obj,
                    meals=default_meals,
                    notes="",
                    totals={
                        "calories": 0, "carbs": 0, "protein": 0, "fats": 0,
                        "saturated_fat": 0, "cholesterol": 0, "sodium": 0,
                        "dietary_fiber": 0, "sugar": 0, "potassium": 0,
                        "trans_fat": 0, "mono_fat": 0, "poly_fat": 0,
                        "vit_a": 0, "vit_c": 0, "vit_d": 0, "net_carbs": 0,
                        "sugar_alc": 0, "sugar_added": 0, "iron": 0, "calcium": 0
                    }
                )
                
                db.session.add(date_record)
                db.session.commit()
                logger.info(f"✅ Created new date record with 3 default meals")
            
            return jsonify({
                'success': True,
                'data': {
                    'id': date_record.id,
                    'date': date_record.date.isoformat(),
                    'meals': date_record.meals or [],
                    'notes': date_record.notes or "",
                    'totals': date_record.totals or {}
                }
            })
        
        elif request.method == 'POST':
            data = request.json or {}
            logger.info(f"🔄 Updating date details for {date}, user: {user_id}")
            
            # Get or create date record
            date_record = UserDate.query.filter_by(user_id=user_id, date=date_obj).first()
            
            if not date_record:
                date_record = UserDate(user_id=user_id, date=date_obj)
                db.session.add(date_record)
            
            # Update fields
            if 'meals' in data:
                date_record.meals = data['meals']
            if 'notes' in data:
                date_record.notes = data['notes']
            if 'totals' in data:
                date_record.totals = data['totals']
            
            date_record.updated_at = datetime.utcnow()
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': 'Date details updated successfully'
            })
    
    except Exception as e:
        logger.error(f"Error in handle_date_details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# NEW: Separate table food management
@app.route('/api/nutrition/user/<user_id>/meal/<int:meal_id>/food/legacy', methods=['POST'])
def add_legacy_food_item(user_id, meal_id):
    """Add a legacy food item (full nutrition data stored)"""
    try:
        data = request.json or {}
        
        # Create legacy food item
        food_item = FoodItemLegacy(
            user_id=user_id,
            meal_id=meal_id,
            food_id=data.get('food_id', ''),
            serving_id=data.get('serving_id', ''),
            servings=float(data.get('servings', 1)),
            foodname=data.get('foodname', ''),
            brandname=data.get('brandname', ''),
            calories=float(data.get('calories', 0)),
            carbs=float(data.get('carbs', 0)),
            protein=float(data.get('protein', 0)),
            fats=float(data.get('fats', 0)),
            saturated_fat=float(data.get('saturated_fat', 0)),
            cholesterol=float(data.get('cholesterol', 0)),
            sodium=float(data.get('sodium', 0)),
            dietary_fiber=float(data.get('dietary_fiber', 0)),
            sugar=float(data.get('sugar', 0)),
            potassium=float(data.get('potassium', 0)),
            trans_fat=float(data.get('trans_fat', 0)),
            mono_fat=float(data.get('mono_fat', 0)),
            poly_fat=float(data.get('poly_fat', 0)),
            vit_a=float(data.get('vit_a', 0)),
            vit_c=float(data.get('vit_c', 0)),
            vit_d=float(data.get('vit_d', 0)),
            net_carbs=float(data.get('net_carbs', 0)),
            sugar_alc=float(data.get('sugar_alc', 0)),
            sugar_added=float(data.get('sugar_added', 0)),
            iron=float(data.get('iron', 0)),
            calcium=float(data.get('calcium', 0)),
            serving_qty=float(data.get('serving_qty', 1)),
            serving_unit=data.get('serving_unit', ''),
            nutrition_multiplier=float(data.get('nutrition_multiplier', 1.0)),
            verified=bool(data.get('verified', False))
        )
        
        db.session.add(food_item)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Legacy food item added successfully',
            'item_id': food_item.id
        })
    
    except Exception as e:
        logger.error(f"Error adding legacy food item: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nutrition/user/<user_id>/meal/<int:meal_id>/food/compliant', methods=['POST'])
def add_compliant_food_item(user_id, meal_id):
    """Add a compliant food item (IDs only - nutrition from FatSecret API)"""
    try:
        data = request.json or {}
        
        # Create compliant food item
        food_item = FoodItemCompliant(
            user_id=user_id,
            meal_id=meal_id,
            food_id=data.get('food_id', ''),
            serving_id=data.get('serving_id', ''),
            servings=float(data.get('servings', 1))
        )
        
        db.session.add(food_item)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Compliant food item added successfully',
            'item_id': food_item.id
        })
    
    except Exception as e:
        logger.error(f"Error adding compliant food item: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nutrition/user/<user_id>/meal/<int:meal_id>/foods', methods=['GET'])
def get_meal_food_items(user_id, meal_id):
    """Get all food items for a meal (both legacy and compliant)"""
    try:
        # Get legacy food items
        legacy_items = FoodItemLegacy.query.filter_by(
            user_id=user_id, 
            meal_id=meal_id
        ).all()
        
        # Get compliant food items
        compliant_items = FoodItemCompliant.query.filter_by(
            user_id=user_id, 
            meal_id=meal_id
        ).all()
        
        # Format legacy items
        legacy_data = []
        for item in legacy_items:
            legacy_data.append({
                'id': item.id,
                'type': 'legacy',
                'food_id': item.food_id,
                'serving_id': item.serving_id,
                'servings': float(item.servings),
                'foodname': item.foodname,
                'brandname': item.brandname,
                'calories': float(item.calories),
                'carbs': float(item.carbs),
                'protein': float(item.protein),
                'fats': float(item.fats),
                # ... include all nutrition fields as needed
                'created_at': item.created_at.isoformat()
            })
        
        # Format compliant items (nutrition will be fetched from FatSecret API)
        compliant_data = []
        for item in compliant_items:
            compliant_data.append({
                'id': item.id,
                'type': 'compliant',
                'food_id': item.food_id,
                'serving_id': item.serving_id,
                'servings': float(item.servings),
                'created_at': item.created_at.isoformat()
            })
        
        return jsonify({
            'success': True,
            'data': {
                'legacy_items': legacy_data,
                'compliant_items': compliant_data,
                'total_items': len(legacy_data) + len(compliant_data)
            }
        })
    
    except Exception as e:
        logger.error(f"Error getting meal food items: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nutrition/user/<user_id>/meal/<int:meal_id>/food/<food_type>/<int:item_id>', methods=['DELETE'])
def delete_food_item(user_id, meal_id, food_type, item_id):
    """Delete a food item by type (legacy or compliant)"""
    try:
        if food_type == 'legacy':
            item = FoodItemLegacy.query.filter_by(
                id=item_id,
                user_id=user_id,
                meal_id=meal_id
            ).first()
        elif food_type == 'compliant':
            item = FoodItemCompliant.query.filter_by(
                id=item_id,
                user_id=user_id,
                meal_id=meal_id
            ).first()
        else:
            return jsonify({'success': False, 'error': 'Invalid food type'}), 400
        
        if not item:
            return jsonify({'success': False, 'error': 'Food item not found'}), 404
        
        db.session.delete(item)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'{food_type.capitalize()} food item deleted successfully'
        })
    
    except Exception as e:
        logger.error(f"Error deleting food item: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nutrition/food/<int:food_id>/servings', methods=['GET'])
def get_serving_sizes(food_id):
    """Get serving sizes for a food item"""
    try:
        user_id = request.args.get('userId')
        
        # Check cache first
        cached_food = FoodCache.query.filter_by(food_id=str(food_id)).first()
        if cached_food and cached_food.serving_sizes:
            return jsonify({'success': True, 'data': cached_food.serving_sizes})
        
        # Use existing endpoint internally
        with current_app.test_client() as client:
            response = client.get(f'/api/food/get?food_id={food_id}')
            if response.status_code == 200:
                food_data = response.get_json()
                servings = food_data.get('food', {}).get('servings', {}).get('serving', [])
                
                # Ensure it's a list
                if not isinstance(servings, list):
                    servings = [servings] if servings else []
                
                # Cache for future use
                if servings:
                    if cached_food:
                        cached_food.serving_sizes = servings
                    else:
                        cached_food = FoodCache(
                            food_id=str(food_id),
                            serving_sizes=servings,
                            created_at=datetime.utcnow()
                        )
                        db.session.add(cached_food)
                    db.session.commit()
                
                return jsonify({'success': True, 'data': servings})
        
        return jsonify({'success': False, 'error': 'Food not found'}), 404
        
    except Exception as e:
        logger.error(f"Error getting serving sizes: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nutrition/user/<user_id>/date/<date>/aggregates', methods=['GET'])
def calculate_day_aggregates_api(user_id, date):
    """Calculate day aggregates from both separate tables and JSON meals"""
    try:
        # Parse date
        try:
            date_obj = datetime.strptime(date, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        # Get date record
        date_record = UserDate.query.filter_by(user_id=user_id, date=date_obj).first()
        
        # Initialize totals
        totals = {
            "calories": 0, "carbs": 0, "protein": 0, "fats": 0,
            "saturated_fat": 0, "cholesterol": 0, "sodium": 0,
            "dietary_fiber": 0, "sugar": 0, "potassium": 0,
            "trans_fat": 0, "mono_fat": 0, "poly_fat": 0,
            "vit_a": 0, "vit_c": 0, "vit_d": 0, "net_carbs": 0,
            "sugar_alc": 0, "sugar_added": 0, "iron": 0, "calcium": 0
        }
        
        meal_ids = []
        
        # Get meal IDs from date record
        if date_record and date_record.meals:
            meal_ids = [meal.get('id') for meal in date_record.meals if meal.get('id')]
        
        # Calculate totals from legacy food items
        if meal_ids:
            legacy_items = FoodItemLegacy.query.filter(
                FoodItemLegacy.user_id == user_id,
                FoodItemLegacy.meal_id.in_(meal_ids)
            ).all()
            
            for item in legacy_items:
                multiplier = float(item.servings) * float(item.nutrition_multiplier)
                totals["calories"] += float(item.calories) * multiplier
                totals["carbs"] += float(item.carbs) * multiplier
                totals["protein"] += float(item.protein) * multiplier
                totals["fats"] += float(item.fats) * multiplier
                totals["saturated_fat"] += float(item.saturated_fat) * multiplier
                totals["cholesterol"] += float(item.cholesterol) * multiplier
                totals["sodium"] += float(item.sodium) * multiplier
                totals["dietary_fiber"] += float(item.dietary_fiber) * multiplier
                totals["sugar"] += float(item.sugar) * multiplier
                totals["potassium"] += float(item.potassium) * multiplier
                totals["trans_fat"] += float(item.trans_fat) * multiplier
                totals["mono_fat"] += float(item.mono_fat) * multiplier
                totals["poly_fat"] += float(item.poly_fat) * multiplier
                totals["vit_a"] += float(item.vit_a) * multiplier
                totals["vit_c"] += float(item.vit_c) * multiplier
                totals["vit_d"] += float(item.vit_d) * multiplier
                totals["net_carbs"] += float(item.net_carbs) * multiplier
                totals["sugar_alc"] += float(item.sugar_alc) * multiplier
                totals["sugar_added"] += float(item.sugar_added) * multiplier
                totals["iron"] += float(item.iron) * multiplier
                totals["calcium"] += float(item.calcium) * multiplier
        
        # Calculate totals from compliant food items (fetch nutrition from FatSecret API)
        if meal_ids:
            compliant_items = FoodItemCompliant.query.filter(
                FoodItemCompliant.user_id == user_id,
                FoodItemCompliant.meal_id.in_(meal_ids)
            ).all()
            
            # For each compliant item, fetch nutrition from FatSecret API
            for item in compliant_items:
                try:
                    # Get nutrition data from FatSecret API
                    with current_app.test_client() as client:
                        response = client.get(f'/api/food/get?food_id={item.food_id}')
                        if response.status_code == 200:
                            food_data = response.get_json()
                            servings_data = food_data.get('food', {}).get('servings', {}).get('serving', [])
                            
                            # Ensure it's a list
                            if not isinstance(servings_data, list):
                                servings_data = [servings_data] if servings_data else []
                            
                            # Find the matching serving
                            serving_nutrition = None
                            for serving in servings_data:
                                if str(serving.get('serving_id', '')) == str(item.serving_id):
                                    serving_nutrition = serving
                                    break
                            
                            if serving_nutrition:
                                multiplier = float(item.servings)
                                
                                # Add nutrition values (with safe float conversion)
                                def safe_float(value, default=0):
                                    try:
                                        return float(value) if value is not None else default
                                    except (ValueError, TypeError):
                                        return default
                                
                                totals["calories"] += safe_float(serving_nutrition.get('calories')) * multiplier
                                totals["carbs"] += safe_float(serving_nutrition.get('carbohydrate')) * multiplier
                                totals["protein"] += safe_float(serving_nutrition.get('protein')) * multiplier
                                totals["fats"] += safe_float(serving_nutrition.get('fat')) * multiplier
                                totals["saturated_fat"] += safe_float(serving_nutrition.get('saturated_fat')) * multiplier
                                totals["cholesterol"] += safe_float(serving_nutrition.get('cholesterol')) * multiplier
                                totals["sodium"] += safe_float(serving_nutrition.get('sodium')) * multiplier
                                totals["dietary_fiber"] += safe_float(serving_nutrition.get('fiber')) * multiplier
                                totals["sugar"] += safe_float(serving_nutrition.get('sugar')) * multiplier
                                totals["potassium"] += safe_float(serving_nutrition.get('potassium')) * multiplier
                                totals["trans_fat"] += safe_float(serving_nutrition.get('trans_fat')) * multiplier
                                totals["mono_fat"] += safe_float(serving_nutrition.get('monounsaturated_fat')) * multiplier
                                totals["poly_fat"] += safe_float(serving_nutrition.get('polyunsaturated_fat')) * multiplier
                                totals["vit_a"] += safe_float(serving_nutrition.get('vitamin_a')) * multiplier
                                totals["vit_c"] += safe_float(serving_nutrition.get('vitamin_c')) * multiplier
                                totals["vit_d"] += safe_float(serving_nutrition.get('vitamin_d')) * multiplier
                                totals["iron"] += safe_float(serving_nutrition.get('iron')) * multiplier
                                totals["calcium"] += safe_float(serving_nutrition.get('calcium')) * multiplier
                                
                                # Calculate net carbs
                                carbs = safe_float(serving_nutrition.get('carbohydrate'))
                                fiber = safe_float(serving_nutrition.get('fiber'))
                                totals["net_carbs"] += (carbs - fiber) * multiplier
                
                except Exception as e:
                    logger.warning(f"Error processing compliant food item {item.id}: {str(e)}")
                    continue
        
        # Also include totals from JSON meals (for backward compatibility)
        if date_record and date_record.meals:
            for meal in date_record.meals:
                food_items = meal.get('food_items', [])
                for food_item in food_items:
                    # Skip items that are already counted in separate tables
                    if food_item.get('_sourceTable') in ['legacy', 'compliant']:
                        continue
                    
                    multiplier = float(food_item.get('servings', 1)) * float(food_item.get('nutrition_multiplier', 1))
                    
                    for key in totals.keys():
                        if key in food_item:
                            totals[key] += float(food_item.get(key, 0)) * multiplier
        
        # Round all values to 2 decimal places
        for key in totals:
            totals[key] = round(totals[key], 2)
        
        # Update the date record with new totals
        if date_record:
            date_record.totals = totals
            date_record.updated_at = datetime.utcnow()
            db.session.commit()
        
        return jsonify({
            'success': True,
            'data': totals,
            'meal_count': len(meal_ids),
            'date': date
        })
        
    except Exception as e:
        logger.error(f"Error calculating day aggregates: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Additional utility routes
@app.route('/api/nutrition/user/<user_id>/dates', methods=['GET'])
def get_user_dates(user_id):
    """Get list of dates with nutrition data for a user"""
    try:
        limit = int(request.args.get('limit', 30))
        offset = int(request.args.get('offset', 0))
        
        dates = UserDate.query.filter_by(user_id=user_id)\
                             .order_by(UserDate.date.desc())\
                             .limit(limit)\
                             .offset(offset)\
                             .all()
        
        dates_data = []
        for date_record in dates:
            dates_data.append({
                'date': date_record.date.isoformat(),
                'meal_count': len(date_record.meals) if date_record.meals else 0,
                'has_notes': bool(date_record.notes),
                'totals': date_record.totals,
                'updated_at': date_record.updated_at.isoformat()
            })
        
        return jsonify({
            'success': True,
            'data': dates_data,
            'total_count': len(dates_data)
        })
        
    except Exception as e:
        logger.error(f"Error getting user dates: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nutrition/food/<food_id>', methods=['GET'])
def get_cached_food_data(food_id):
    """Get cached food data"""
    try:
        cached_food = FoodCache.query.filter_by(food_id=food_id).first()
        
        if cached_food:
            return jsonify({
                'success': True,
                'data': {
                    'food_data': cached_food.food_data,
                    'serving_sizes': cached_food.serving_sizes,
                    'cached_at': cached_food.updated_at.isoformat()
                }
            })
        else:
            return jsonify({'success': False, 'error': 'Food not found in cache'}), 404
            
    except Exception as e:
        logger.error(f"Error getting cached food data: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Barcode utility functions
def upce_to_upca(upce):
    """Convert UPC-E (8 digits) to UPC-A (12 digits)"""
    if len(upce) != 8 or not upce.isdigit():
        raise ValueError("UPC-E must be exactly 8 digits")
    
    number_system = upce[0]
    last_digit = upce[7]
    
    if last_digit in '012':
        upca_body = f"{upce[1:3]}{last_digit}0000{upce[3:6]}"
    elif last_digit == '3':
        upca_body = f"{upce[1:4]}00000{upce[4:6]}"
    elif last_digit == '4':
        upca_body = f"{upce[0:4]}00000{upce[4]}"
    elif last_digit in '56789':
        upca_body = f"{upce[0:5]}0000{last_digit}"
    else:
        raise ValueError("Invalid UPC-E format")

    upca11 = number_system + upca_body
    check_digit = calculate_upc_check_digit(upca11)
    full_upca = upca11 + check_digit
    return full_upca.zfill(13)

def calculate_upc_check_digit(upc11):
    """Calculate the 12th digit (check digit) for a UPC-A code"""
    if len(upc11) != 11 or not upc11.isdigit():
        raise ValueError("UPC-A base must be 11 digits")

    odd_sum = sum(int(upc11[i]) for i in range(0, 11, 2))
    even_sum = sum(int(upc11[i]) for i in range(1, 11, 2))
    total = (odd_sum * 3) + even_sum
    check_digit = (10 - (total % 10)) % 10
    return str(check_digit)

@app.route("/api/food/barcode/debug", methods=["GET"])
@token_required
def debug_barcode(token):
    """Debug endpoint for barcode lookup - returns raw API response"""
    barcode = request.args.get("barcode", "")
    region = request.args.get("region", "")
    language = request.args.get("language", "")
    
    if not barcode:
        return jsonify({"error": "Missing required parameter: barcode"}), 400
    
    # Try with both original and zero-padded barcode if needed
    results = []
    barcodes_to_try = [barcode]
    
    # If barcode is not 13 digits, add a zero-padded version
    if len(barcode) < 13 and barcode.isdigit():
        zero_padded = barcode.zfill(13)
        barcodes_to_try.append(zero_padded)
    
    for test_barcode in barcodes_to_try:
        try:
            # Prepare request parameters
            api_url = "https://platform.fatsecret.com/rest/server.api"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Bearer {token}"
            }
            
            # Build parameters
            params = {
                "method": "food.find_id_for_barcode",
                "barcode": test_barcode,
                "format": "json"
            }
            
            # Add optional parameters if provided
            if region:
                params["region"] = region
                if language:
                    params["language"] = language
            
            logger.info(f"Looking up barcode: {test_barcode}")
            
            # Call API directly without retry for debugging
            response = requests.post(api_url, headers=headers, data=params)
            
            # Try to parse as JSON if possible
            try:
                response_json = response.json()
                results.append({
                    "barcode": test_barcode,
                    "status_code": response.status_code,
                    "response": response_json
                })
            except:
                # If not JSON, return raw text
                results.append({
                    "barcode": test_barcode,
                    "status_code": response.status_code,
                    "response_text": response.text
                })
                
        except Exception as e:
            results.append({
                "barcode": test_barcode,
                "error": str(e)
            })
    
    return jsonify({
        "results": results,
        "token_info": {
            "token_active": token_info["access_token"] is not None,
            "expires_in": max(0, int(token_info["expiry_time"] - time.time())) if token_info["expiry_time"] else 0
        }
    })

@app.route("/api/food/nlp", methods=["POST"])
@token_required
def process_food_text(token):
    """Process natural language descriptions of foods and return structured food data"""
    try:
        # Get JSON data from request
        request_data = request.json
        
        if not request_data:
            return jsonify({"error": "Missing request body"}), 400
        
        # Validate required fields
        if "user_input" not in request_data:
            return jsonify({"error": "Missing required field: user_input"}), 400
        
        # Check user_input length
        if len(request_data["user_input"]) > 1000:
            return jsonify({"error": "user_input exceeds maximum length of 1000 characters"}), 400
        
        # Prepare request parameters
        api_url = "https://platform.fatsecret.com/rest/natural-language-processing/v1"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        # Prepare request body
        nlp_request = {
            "user_input": request_data["user_input"]
        }
        
        # Add optional parameters if provided
        if "region" in request_data:
            nlp_request["region"] = request_data["region"]
            
            if "language" in request_data:
                nlp_request["language"] = request_data["language"]
                
        if "include_food_data" in request_data:
            nlp_request["include_food_data"] = request_data["include_food_data"]
            
        if "eaten_foods" in request_data and isinstance(request_data["eaten_foods"], list):
            # Validate each eaten food has required fields
            valid_eaten_foods = []
            for food in request_data["eaten_foods"]:
                if isinstance(food, dict) and "food_id" in food and "food_name" in food:
                    valid_food = {
                        "food_id": food["food_id"],
                        "food_name": food["food_name"]
                    }
                    
                    # Add optional fields if present
                    if "brand" in food:
                        valid_food["brand"] = food["brand"]
                    if "serving_description" in food:
                        valid_food["serving_description"] = food["serving_description"]
                    if "serving_size" in food:
                        valid_food["serving_size"] = food["serving_size"]
                        
                    valid_eaten_foods.append(valid_food)
            
            if valid_eaten_foods:
                nlp_request["eaten_foods"] = valid_eaten_foods
        
        logger.info(f"Sending NLP request: {nlp_request}")
        
        # Make the API request
        try:
            response = requests.post(api_url, headers=headers, json=nlp_request)
            response.raise_for_status()
            
            # Parse and return the response
            return jsonify(response.json())
            
        except requests.RequestException as e:
            logger.error(f"NLP API request failed: {str(e)}")
            
            # Try to get more detailed error information
            error_message = "API request failed"
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    if 'error' in error_data:
                        error_message = error_data['error'].get('message', error_message)
                except:
                    error_message = f"API request failed with status code {e.response.status_code}"
            
            return jsonify({"error": error_message}), 500
            
    except Exception as e:
        logger.error(f"Error in process_food_text: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/foods/autocomplete", methods=["GET"])
@token_required
def autocomplete_food(token):
    """Autocomplete food search suggestions"""
    expression = request.args.get("expression", "")
    max_results = request.args.get("max_results", "10")
    region = request.args.get("region", "")
    
    if not expression:
        return jsonify({"error": "Missing required parameter: expression"}), 400
    
    try:
        # Prepare request parameters
        api_url = "https://platform.fatsecret.com/rest/food/autocomplete/v2"
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        # Build query parameters
        params = {
            "expression": expression,
            "format": "json"  # Always use JSON format
        }
        
        # Add optional parameters if provided
        if max_results:
            # Ensure max_results doesn't exceed 10 (API limit)
            try:
                max_results_int = int(max_results)
                if max_results_int > 10:
                    max_results = "10"
                    logger.warning("max_results limited to 10 (API maximum)")
                params["max_results"] = max_results
            except ValueError:
                logger.warning(f"Invalid max_results value: {max_results}, using default")
        
        if region:
            params["region"] = region
        
        logger.info(f"Autocomplete request: expression='{expression}', max_results={max_results}, region='{region}'")
        
        # Make API request with GET method
        response = requests.get(api_url, headers=headers, params=params)
        
        # Check for API error responses even with 200 status
        if response.status_code == 200 and '"error"' in response.text:
            error_json = response.json()
            if 'error' in error_json:
                error_msg = error_json['error'].get('message', 'Unknown API error')
                
                # If token is invalid, refresh and retry
                if 'token is invalid' in error_msg.lower():
                    logger.warning("Invalid token detected, refreshing token and retrying...")
                    headers["Authorization"] = f"Bearer {get_token(force_refresh=True)}"
                    response = requests.get(api_url, headers=headers, params=params)
                else:
                    raise Exception(f"API error: {error_msg}")
        
        response.raise_for_status()
        
        # Parse response
        try:
            json_data = response.json()
            return jsonify(json_data)
        except ValueError as json_err:
            logger.error(f"Failed to parse API response: {str(json_err)}")
            return jsonify({"error": f"Failed to parse API response: {str(json_err)}"}), 500
            
    except requests.RequestException as e:
        logger.error(f"Autocomplete request failed: {str(e)}")
        
        # Try to get more detailed error information
        error_message = "API request failed"
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                if 'error' in error_data:
                    error_message = error_data['error'].get('message', error_message)
            except:
                error_message = f"API request failed with status code {e.response.status_code}"
        
        return jsonify({"error": error_message}), 500
    except Exception as e:
        logger.error(f"Error in autocomplete_food: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/text-to-food", methods=["POST"])
@token_required
def text_to_food_analysis(token):
    """Analyze text description of foods and return nutrition data"""
    try:
        logger.info("🍽️ Text-to-food analysis request received")
        
        # Get JSON data from request
        request_data = request.json
        
        if not request_data:
            return jsonify({"error": "Missing request body"}), 400
        
        # Check if text is present
        if 'text' not in request_data:
            return jsonify({"error": "Missing required field: text"}), 400
        
        transcribed_text = request_data['text'].strip()
        
        if not transcribed_text:
            return jsonify({"error": "Empty text field"}), 400
        
        # Get optional parameters
        region = request_data.get('region', 'US')
        language = request_data.get('language', 'en')
        include_food_data = request_data.get('include_food_data', True)
        
        logger.info(f"Processing text: '{transcribed_text}'")
        logger.info(f"Parameters - region: {region}, language: {language}, include_food_data: {include_food_data}")
        
        # Process text with FatSecret NLP
        logger.info("🍽️ Processing text with FatSecret NLP...")
        
        nlp_request = {
            "user_input": transcribed_text,
            "region": region,
            "include_food_data": include_food_data
        }
        
        # Add language if region is specified and language is provided
        if region and language:
            nlp_request["language"] = language
        
        # Call FatSecret NLP API
        api_url = "https://platform.fatsecret.com/rest/natural-language-processing/v1"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        logger.info(f"Sending NLP request: {nlp_request}")
        
        nlp_response = requests.post(api_url, headers=headers, json=nlp_request)
        
        logger.info(f"NLP Response status: {nlp_response.status_code}")
        
        if nlp_response.status_code == 200:
            nlp_data = nlp_response.json()
            
            # Extract food count for logging
            food_count = 0
            if "food_response" in nlp_data and isinstance(nlp_data["food_response"], list):
                food_count = len(nlp_data["food_response"])
            
            logger.info(f"✅ NLP analysis successful: {food_count} foods identified")
            
            # Return result
            return jsonify({
                "success": True,
                "text": transcribed_text,
                "food_analysis": nlp_data,
                "metadata": {
                    "foods_identified": food_count,
                    "region": region,
                    "language": language,
                    "processing_time": "< 1s"
                }
            })
        else:
            # NLP failed
            try:
                nlp_error = nlp_response.json()
                error_message = nlp_error.get('error', {}).get('message', 'NLP processing failed')
            except:
                error_message = f"NLP processing failed with status {nlp_response.status_code}"
            
            logger.warning(f"⚠️ NLP failed: {error_message}")
            
            return jsonify({
                "success": False,
                "text": transcribed_text,
                "error": error_message,
                "suggestion": "Food analysis failed. Please try rephrasing your food description."
            }), 500
        
    except Exception as e:
        logger.error(f"❌ Error in text_to_food_analysis: {str(e)}")
        
        return jsonify({
            "success": False,
            "error": str(e),
            "details": str(e) if app.debug else None
        }), 500

@app.route("/api/food/image-recognition", methods=["POST"])
@token_required
def recognize_food_image(token):
    """Identify food items and their nutritional information from an image"""
    try:
        logger.info("🔍 Image recognition request received")
        logger.info(f"📥 Request content type: {request.content_type}")
        logger.info(f"📥 Request method: {request.method}")
        
        # Get JSON data from request
        request_data = request.json
        logger.info(f"📦 Request data type: {type(request_data)}")
        logger.info(f"📦 Request data keys: {list(request_data.keys()) if request_data else 'None'}")
        
        if not request_data:
            logger.error("❌ Missing request body")
            return jsonify({"error": "Missing request body"}), 400
        
        # Validate required fields
        if "image_b64" not in request_data:
            logger.error("❌ Missing image_b64 field")
            logger.error(f"Available fields: {list(request_data.keys())}")
            return jsonify({"error": "Missing required field: image_b64"}), 400
        
        image_b64 = request_data["image_b64"]
        logger.info(f"📏 Image base64 length: {len(image_b64) if image_b64 else 0}")
        logger.info(f"🔍 Image base64 type: {type(image_b64)}")
        
        if not image_b64:
            logger.error("❌ Empty image_b64 field")
            return jsonify({"error": "image_b64 field is empty"}), 400
        
        if not isinstance(image_b64, str):
            logger.error(f"❌ image_b64 wrong type: {type(image_b64)}")
            return jsonify({"error": "image_b64 must be a string"}), 400
        
        # Check image_b64 length (API limit is 1,148,549 characters)
        if len(image_b64) > 1148549:
            logger.error(f"❌ Image too large: {len(image_b64)} > 1,148,549")
            return jsonify({"error": "image_b64 exceeds maximum length of 1,148,549 characters"}), 400
        
        # Basic validation for base64 format
        logger.info(f"🔍 Base64 preview (first 50 chars): {image_b64[:50]}")
        
        # Check if it looks like base64 (basic check)
        if not image_b64.replace('+', '').replace('/', '').replace('=', '').isalnum():
            logger.error("❌ Invalid base64 format")
            return jsonify({"error": "image_b64 appears to be invalid base64 format"}), 400
        
        # Prepare request parameters
        api_url = "https://platform.fatsecret.com/rest/image-recognition/v2"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        # Prepare request body
        recognition_request = {
            "image_b64": image_b64
        }
        
        # Add optional parameters if provided
        if "region" in request_data and request_data["region"]:
            recognition_request["region"] = request_data["region"]
            
            # Language is only valid when region is also specified
            if "language" in request_data and request_data["language"]:
                recognition_request["language"] = request_data["language"]
                
        if "include_food_data" in request_data:
            recognition_request["include_food_data"] = bool(request_data["include_food_data"])
        
        logger.info(f"✅ All validations passed, sending to FatSecret API")
        logger.info(f"🚀 Request to: {api_url}")
        logger.info(f"📦 Request keys: {list(recognition_request.keys())}")
        
        # Make the API request with retry logic
        try:
            response = call_api(api_url, headers, recognition_request)
            
            # Parse and return the response
            json_data = response.json()
            
            # Log successful recognition
            if "food_response" in json_data:
                food_count = len(json_data["food_response"])
                logger.info(f"✅ Image recognition successful: {food_count} foods identified")
            
            return jsonify(json_data)
            
        except requests.RequestException as e:
            logger.error(f"❌ FatSecret API request failed: {str(e)}")
            
            # Try to get more detailed error information
            error_message = "Image recognition API request failed"
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"❌ FatSecret response status: {e.response.status_code}")
                logger.error(f"❌ FatSecret response body: {e.response.text}")
                try:
                    error_data = e.response.json()
                    if 'error' in error_data:
                        error_message = error_data['error'].get('message', error_message)
                        
                        # Handle specific error codes
                        if 'error_code' in error_data['error']:
                            error_code = error_data['error']['error_code']
                            if error_code == 211:
                                error_message = "Image contains only nutrition facts panel - please submit an image of actual food items"
                            elif error_code in [212, 213]:
                                error_message = "Invalid image format or corrupted image data"
                except:
                    error_message = f"Image recognition failed with status code {e.response.status_code}"
            
            return jsonify({"error": error_message}), 500
            
    except Exception as e:
        logger.error(f"❌ Unexpected error in recognize_food_image: {str(e)}")
        logger.error(f"❌ Exception type: {type(e)}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    """API home/info page"""
    return jsonify({
        "name": "FatSecret API Server + Nutrition Tracker",
        "description": "A Flask server that provides access to FatSecret API and nutrition tracking with separate food tables",
        "endpoints": {
            # Existing FatSecret endpoints
            "/api/foods/search": "Search for foods by name",
            "/api/food/get": "Get detailed information about a specific food by ID",
            "/api/food/barcode": "Find food information using a barcode",
            "/api/food/barcode/debug": "Debug endpoint for barcode lookup",
            "/api/food/nlp": "Process natural language food descriptions (POST)",
            "/api/text-to-food": "Analyze text description of foods (POST)",
            "/api/food/image-recognition": "Identify food items from images (POST)",
            "/api/foods/autocomplete": "Autocomplete food search suggestions",
            
            # Nutrition tracking endpoints
            "/api/nutrition/user/<user_id>": "Get or update user profile",
            "/api/nutrition/user/<user_id>/date/<date>": "Get or update nutrition data for a specific date",
            "/api/nutrition/user/<user_id>/dates": "Get list of dates with data",
            "/api/nutrition/food/<food_id>": "Get cached food data",
            
            # NEW: Separate table food management
            "/api/nutrition/user/<user_id>/meal/<meal_id>/food/legacy": "Add legacy food item (POST)",
            "/api/nutrition/user/<user_id>/meal/<meal_id>/food/compliant": "Add compliant food item (POST)",
            "/api/nutrition/user/<user_id>/meal/<meal_id>/foods": "Get all food items for meal",
            "/api/nutrition/user/<user_id>/meal/<meal_id>/food/<type>/<item_id>": "Delete food item by type (DELETE)",
            "/api/nutrition/user/<user_id>/date/<date>/aggregates": "Calculate day aggregates from both tables"
        },
        "database_tables": {
            "food_items_legacy": "Stores full nutrition data for legacy items",
            "food_items_compliant": "Stores only IDs for compliant items (nutrition from FatSecret API)",
            "user_dates": "Main user nutrition data (JSON-based meals)",
            "food_cache": "Caches FatSecret API responses"
        },
        "status": "Token is " + ("active" if token_info["access_token"] else "not initialized"),
        "expires_in": max(0, int(token_info["expiry_time"] - time.time())) if token_info["expiry_time"] else 0
    })

# Create database tables on startup
with app.app_context():
    try:
        db.create_all()
        logger.info("✅ Database tables created successfully!")
        
        # Optional: Print table names to verify
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        logger.info(f"📋 Available tables: {tables}")
        
    except Exception as e:
        logger.error(f"❌ Error creating database tables: {str(e)}")

# Initialize token on startup for development
# if __name__ == "__main__":
#     try:
#         get_token()
#         logger.info("Initial token successfully obtained")
#     except Exception as e:
#         logger.error(f"Failed to obtain initial token: {str(e)}")
#         logger.info("Server will retry token acquisition on first API request")
    
#     # Run the app
#     port = int(os.environ.get("PORT", 5001))
#     logger.info(f"Starting server on port {port}")
#     app.run(debug=True, host="0.0.0.0", port=port)