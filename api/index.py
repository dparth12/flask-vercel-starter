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


def call_api(url, headers, params):
    """Call FatSecret API with retry logic for failures"""
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"API request attempt {attempt + 1}")
            
            response = requests.post(url, headers=headers, data=params)
            logger.info(f"Response status: {response.status_code}")
            
            # Check for API error responses even with 200 status
            if response.status_code == 200 and '"error"' in response.text:
                error_json = response.json()
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
    """Find food information by barcode"""
    barcode = request.args.get("barcode", "")
    region = request.args.get("region", "")
    language = request.args.get("language", "")

    if not barcode:
        return jsonify({"error": "Missing required parameter: barcode"}), 400

    if not barcode.isdigit():
        return jsonify({"error": "Invalid barcode format. Must contain only digits"}), 400

    # Handle UPC-E (6-digit or 8-digit with number system + check digit)
    if len(barcode) == 6:
        try:
            original_barcode = barcode
            barcode = convert_upce_to_gtin13(barcode, number_system='0')
            logger.info(f"Converted 6-digit UPC-E {original_barcode} to GTIN-13 {barcode}")
        except ValueError as e:
            return jsonify({"error": f"Invalid UPC-E barcode: {str(e)}"}), 400

    elif len(barcode) == 8:
        try:
            number_system = barcode[0]
            upce_body = barcode[1:7]
            check_digit = barcode[7]  # optional validation step
            original_barcode = barcode
            barcode = convert_upce_to_gtin13(upce_body, number_system=number_system)
            logger.info(f"Converted 8-digit UPC-E {original_barcode} to GTIN-13 {barcode}")
        except ValueError as e:
            return jsonify({"error": f"Invalid UPC-E barcode: {str(e)}"}), 400

    # Zero-pad to GTIN-13 if shorter than 13
    elif len(barcode) < 13:
        original_barcode = barcode
        barcode = barcode.zfill(13)
        logger.info(f"Zero-padded barcode from {original_barcode} to {barcode}")
    elif len(barcode) > 13:
        return jsonify({"error": "Invalid barcode format. Must not exceed 13 digits"}), 400

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

        logger.info(f"Looking up barcode: {barcode}")
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


def convert_upce_to_gtin13(upce, number_system='0'):
    """Convert UPC-E (6-digit) to GTIN-13 using number system and calculated check digit"""
    if len(upce) != 6:
        raise ValueError("UPC-E must be exactly 6 digits")

    last_digit = upce[-1]

    if last_digit in '012':
        upca_body = f"{upce[0:2]}{last_digit}0000{upce[2:5]}"
    elif last_digit == '3':
        upca_body = f"{upce[0:3]}00000{upce[3:5]}"
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


'''
if __name__ == "__main__":
    # Initialize token on startup
    try:
        get_token()
        logger.info("Initial token successfully obtained")
    except Exception as e:
        logger.error(f"Failed to obtain initial token: {str(e)}")
        logger.info("Server will retry token acquisition on first API request")
    
    # Run the app
    port = int(os.environ.get("PORT", 5001))
    logger.info(f"Starting server on port {port}")
    app.run(debug=True, host="0.0.0.0", port=port)
'''