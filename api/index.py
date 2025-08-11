from flask import Flask, request, jsonify
import requests
import time
import os
import logging
from functools import wraps
import threading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# FatSecret API credentials
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')

# Token storage
token_info = {
    "access_token": None,
    "expiry_time": 0,
    "refresh_lock": threading.Lock()
}

# Constants
TOKEN_REFRESH_THRESHOLD = 3600  # Refresh token if less than 1 hour remaining (in seconds)
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def get_token(force_refresh=False):
    """Get a valid access token, refreshing if necessary"""
    current_time = time.time()
    
    # Check if we need to refresh the token
    needs_refresh = (
        force_refresh or 
        token_info["access_token"] is None or 
        token_info["expiry_time"] - current_time < TOKEN_REFRESH_THRESHOLD
    )
    
    if needs_refresh:
        with token_info["refresh_lock"]:
            # Double-check if token was refreshed by another thread while waiting for lock
            current_time = time.time()
            if (force_refresh or 
                token_info["access_token"] is None or 
                token_info["expiry_time"] - current_time < TOKEN_REFRESH_THRESHOLD):
                
                refresh_token_with_retry()
    
    return token_info["access_token"]


def refresh_token_with_retry():
    """Refresh the access token with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            refresh_token()
            logger.info(f"Token refreshed successfully on attempt {attempt + 1}")
            return
        except Exception as e:
            logger.error(f"Token refresh attempt {attempt + 1} failed: {str(e)}")
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error("All token refresh attempts failed")
                raise


def refresh_token():
    """Request a new access token from FatSecret API"""
    try:
        token_url = "https://oauth.fatsecret.com/connect/token"
        payload = "grant_type=client_credentials"  # No scope specified to get all available scopes
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        logger.info("Requesting new access token")
        response = requests.post(
            token_url,
            auth=(CLIENT_ID, CLIENT_SECRET),
            headers=headers,
            data=payload
        )
        
        response.raise_for_status()
        token_data = response.json()
        
        # Update token info
        token_info["access_token"] = token_data["access_token"]
        token_info["expiry_time"] = time.time() + token_data["expires_in"]
        
        # Log the token scope if available in the response
        if "scope" in token_data:
            logger.info(f"Token refreshed with scope: {token_data['scope']}")
        else:
            logger.info(f"Token refreshed, expires in {token_data['expires_in']} seconds")
        
        # Schedule a background refresh before expiry
        schedule_token_refresh(token_data["expires_in"])
        
    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        raise


def schedule_token_refresh(expires_in):
    """Schedule a background token refresh before the token expires"""
    refresh_time = expires_in - TOKEN_REFRESH_THRESHOLD
    if refresh_time > 0:
        def delayed_refresh():
            time.sleep(refresh_time)
            try:
                logger.info("Performing scheduled token refresh")
                get_token(force_refresh=True)
            except Exception as e:
                logger.error(f"Scheduled token refresh failed: {str(e)}")
        
        # Start background thread for token refresh
        thread = threading.Thread(target=delayed_refresh)
        thread.daemon = True
        thread.start()
        logger.info(f"Scheduled token refresh in {refresh_time} seconds")


def token_required(f):
    """Decorator to ensure a valid token is available"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token()
        return f(token, *args, **kwargs)
    return decorated


# def call_api(url, headers, params):
#     """Call FatSecret API with retry logic for failures"""
#     for attempt in range(MAX_RETRIES):
#         try:
#             logger.info(f"API request attempt {attempt + 1}")
            
#             response = requests.post(url, headers=headers, data=params)
#             logger.info(f"Response status: {response.status_code}")
            
#             # Check for API error responses even with 200 status
#             if response.status_code == 200 and '"error"' in response.text:
#                 error_json = response.json()
#                 if 'error' in error_json:
#                     error_msg = error_json['error'].get('message', 'Unknown API error')
                    
#                     # If token is invalid, refresh and retry
#                     if 'token is invalid' in error_msg.lower():
#                         if attempt < MAX_RETRIES - 1:
#                             logger.warning("Invalid token detected, refreshing token and retrying...")
#                             headers["Authorization"] = f"Bearer {get_token(force_refresh=True)}"
#                             continue
                    
#                     raise Exception(f"API error: {error_msg}")
            
#             response.raise_for_status()
#             return response
            
#         except requests.RequestException as e:
#             logger.error(f"Request failed on attempt {attempt + 1}: {str(e)}")
            
#             # Retry only for certain errors
#             if attempt < MAX_RETRIES - 1:
#                 # Check if we need to refresh token
#                 if hasattr(e, 'response') and e.response is not None:
#                     if e.response.status_code in (401, 403):
#                         logger.warning("Authentication error, refreshing token before retry")
#                         headers["Authorization"] = f"Bearer {get_token(force_refresh=True)}"
                
#                 wait_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
#                 logger.info(f"Retrying in {wait_time} seconds...")
#                 time.sleep(wait_time)
#             else:
#                 logger.error("All API request attempts failed")
#                 raise

def call_api(url, headers, params):
    """Call FatSecret API with retry logic for failures"""
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"API request attempt {attempt + 1}")
            logger.info(f"URL: {url}")
            logger.info(f"Headers: {headers}")
            
            # Handle both form data and JSON requests
            if isinstance(params, dict) and 'image_b64' in params:
                # This is an image recognition request - send as JSON
                logger.info("Sending as JSON request (image recognition)")
                logger.info(f"JSON payload keys: {list(params.keys())}")
                response = requests.post(url, headers=headers, json=params)
            else:
                # This is a regular API request - send as form data
                logger.info("Sending as form data request")
                logger.info(f"Form data: {params}")
                response = requests.post(url, headers=headers, data=params)
            
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response headers: {dict(response.headers)}")
            logger.info(f"Response body preview: {response.text[:500]}")
            
            # Check for API error responses even with 200 status
            if response.status_code == 200 and '"error"' in response.text:
                error_json = response.json()
                logger.error(f"API returned error in 200 response: {error_json}")
                if 'error' in error_json:
                    error_msg = error_json['error'].get('message', 'Unknown API error')
                    
                    # If token is invalid, refresh and retry
                    if 'token is invalid' in error_msg.lower():
                        if attempt < MAX_RETRIES - 1:
                            logger.warning("Invalid token detected, refreshing token and retrying...")
                            headers["Authorization"] = f"Bearer {get_token(force_refresh=True)}"
                            continue
                    
                    raise Exception(f"API error: {error_msg}")
            
            response.raise_for_status()
            return response
            
        except requests.RequestException as e:
            logger.error(f"Request failed on attempt {attempt + 1}: {str(e)}")
            
            # Retry only for certain errors
            if attempt < MAX_RETRIES - 1:
                # Check if we need to refresh token
                if hasattr(e, 'response') and e.response is not None:
                    if e.response.status_code in (401, 403):
                        logger.warning("Authentication error, refreshing token before retry")
                        headers["Authorization"] = f"Bearer {get_token(force_refresh=True)}"
                
                wait_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error("All API request attempts failed")
                raise

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/api/foods/search", methods=["GET"])
@token_required
def search_food(token):
    """Search for foods by name"""
    search_term = request.args.get("query", "")
    page = request.args.get("page", "0")
    max_results = request.args.get("max_results", "50")
    
    try:
        # Prepare request parameters
        api_url = "https://platform.fatsecret.com/rest/server.api"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {token}"
        }
        
        # Build parameters
        params = {
            "method": "foods.search.v3",
            "search_expression": search_term,
            "format": "json"
        }
        
        # Add optional parameters if provided
        if page:
            params["page_number"] = page
        if max_results:
            params["max_results"] = max_results
        
        # Call API with retry logic
        response = call_api(api_url, headers, params)
        
        # Parse response
        try:
            json_data = response.json()
            return jsonify(json_data)
        except ValueError as json_err:
            logger.error(f"Failed to parse API response: {str(json_err)}")
            return jsonify({"error": f"Failed to parse API response: {str(json_err)}"}), 500
            
    except Exception as e:
        logger.error(f"Error in search_food: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/food/get", methods=["GET"])
@token_required
def get_food(token):
    """Get detailed information about a specific food"""
    food_id = request.args.get("food_id", "")
    
    if not food_id:
        return jsonify({"error": "Missing required parameter: food_id"}), 400
    
    try:
        # Prepare request parameters
        api_url = "https://platform.fatsecret.com/rest/server.api"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {token}"
        }
        
        # Build parameters
        params = {
            "method": "food.get",
            "food_id": food_id,
            "format": "json"
        }
        
        # Call API with retry logic
        response = call_api(api_url, headers, params)
        
        # Parse response
        try:
            json_data = response.json()
            return jsonify(json_data)
        except ValueError as json_err:
            logger.error(f"Failed to parse API response: {str(json_err)}")
            return jsonify({"error": f"Failed to parse API response: {str(json_err)}"}), 500
            
    except Exception as e:
        logger.error(f"Error in get_food: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/food/barcode", methods=["GET"])
@token_required
def find_food_by_barcode(token):
    """Find food information by barcode - expects GTIN-13 formatted barcode from client"""
    barcode = request.args.get("barcode", "")
    region = request.args.get("region", "")
    language = request.args.get("language", "")

    if not barcode:
        return jsonify({"error": "Missing required parameter: barcode"}), 400

    # Basic validation - client should have already processed the barcode
    if not barcode.isdigit():
        return jsonify({"error": "Invalid barcode format. Must contain only digits"}), 400
    
    if len(barcode) != 13:
        return jsonify({"error": "Barcode must be 13 digits (GTIN-13 format)"}), 400

    try:
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

        logger.info(f"Looking up GTIN-13 barcode: {barcode}")
        response = call_api(api_url, headers, params)

        try:
            barcode_json = response.json()
            logger.info(f"Barcode lookup response: {barcode_json}")

            food_id = None
            if "food_id" in barcode_json:
                if isinstance(barcode_json["food_id"], dict) and "value" in barcode_json["food_id"]:
                    food_id = barcode_json["food_id"]["value"]
                else:
                    food_id = barcode_json["food_id"]

            if food_id and str(food_id) != "0":
                # Get detailed food information
                food_params = {
                    "method": "food.get",
                    "food_id": food_id,
                    "format": "json"
                }

                food_response = call_api(api_url, headers, food_params)
                food_data = food_response.json()

                return jsonify({
                    "barcode": barcode,
                    "food_id": food_id,
                    "food_details": food_data
                })
            else:
                return jsonify({
                    "barcode": barcode,
                    "message": "No food found for this barcode",
                    "raw_response": barcode_json
                })

        except ValueError as json_err:
            logger.error(f"Failed to parse API response: {str(json_err)}")
            return jsonify({
                "error": f"Failed to parse API response: {str(json_err)}",
                "barcode": barcode
            }), 500

    except Exception as e:
        logger.error(f"Error in find_food_by_barcode: {str(e)}")
        return jsonify({"error": str(e), "barcode": barcode}), 500





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
            
            # Get raw response text
            response_text = response.text
            
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
                    "response_text": response_text
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


@app.route("/", methods=["GET"])
def home():
    """API home/info page"""
    return jsonify({
        "name": "FatSecret API Server",
        "description": "A Flask server that provides access to FatSecret API",
        "endpoints": {
            "/api/foods/search": "Search for foods by name",
            "/api/food/get": "Get detailed information about a specific food by ID",
            "/api/food/barcode": "Find food information using a barcode",
            "/api/food/barcode/debug": "Debug endpoint for barcode lookup",
            "/api/food/nlp": "Process natural language food descriptions (POST)"
            
        },
        "status": "Token is " + ("active" if token_info["access_token"] else "not initialized"),
        "expires_in": max(0, int(token_info["expiry_time"] - time.time())) if token_info["expiry_time"] else 0
    })

@app.route("/api/food/nlp", methods=["POST"])
@token_required
def process_food_text(token):
    """
    Process natural language descriptions of foods and return structured food data
    
    This endpoint accepts a text description of food items and returns identified
    foods with their nutritional information.
    
    Request body:
    {
      "user_input": "A toast with ham and cheese, an apple, a banana and a cappuccino",
      "region": "US",  // optional
      "language": "en",  // optional
      "include_food_data": true,  // optional
      "eaten_foods": [  // optional
        {
          "food_id": 3092,
          "food_name": "egg",
          "brand": null,
          "serving_description": "",
          "serving_size": 1
        }
      ]
    }
    """
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
    """
    Analyze text description of foods and return nutrition data
    
    This endpoint:
    1. Accepts transcribed text from the app
    2. Processes text with FatSecret NLP
    3. Returns identified foods and nutrition data
    
    JSON body:
    {
        "text": "I ate an apple and a banana for breakfast",
        "region": "US",  // optional
        "language": "en",  // optional
        "include_food_data": true  // optional
    }
    """
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
    """
    Identify food items and their nutritional information from an image
    """
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