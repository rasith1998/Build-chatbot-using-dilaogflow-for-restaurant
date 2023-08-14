from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse
from datetime import datetime, time, timedelta
import db_helper
import generic_helper

app = FastAPI()

# Mount the static directory to serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    # Use raw string and proper path separator
    html_path = Path(r"templates\home.html")  # Adjust the path if needed
    return FileResponse(html_path)

inprogress_orders = {} #Global dictionary

@app.post("/")
async def webhook(request: Request):
    # Retrieve the JSON data from the request
    payload = await request.json()
    # Extract the necessary information from the payload
    # based on the structure of the WebhookRequest from Dialogflow

    action = payload['queryResult']['action'] if 'action' in payload else ""
    intent = payload['queryResult']['intent']['displayName']
    parameters = payload['queryResult']['parameters']
    output_contexts = payload['queryResult']['outputContexts']
    session_id=generic_helper.extract_session_id(output_contexts[0]["name"])

    current_datetime = datetime.now()

    # It will add store time
    if not is_shop_open(current_datetime):
        if action=='input.welcome':
            fulfillment_text = {
                "fulfillmentMessages": [{"text": {"text": ["I'm sorry, but I'm currently not available.Here are our store hours Mon 10 am - 10 PM and Tue 10 am - 10 pm."]}}]}
            return fulfillment_text
        else:
            fulfillment_text = {
                "fulfillmentMessages": [{"text": {"text": ["I'm sorry, but I'm currently not available.Here are our store hours Mon 10 am - 10 PM and Tue 10 am - 10 pm."]}}]}
            return fulfillment_text
    

    else:
        intent_handler_dict = {
            'order.add-context: ongoing-order': add_to_order,
            'order.remove-context: ongoing-order': remove_from_order,
            'order.complete - context: ongoing-order': complete_order,
            'track.order - context: ongoing-tracking': track_order,
            'Default Welcome Intent':welcome_intent,
        }
        return intent_handler_dict[intent](parameters,session_id)
    



def is_shop_open(current_datetime):
        # current_datetime = datetime.now()
        current_weekday = current_datetime.weekday()  # 0 = Monday, 1 = Tuesday, ...
        current_time = current_datetime.time()
        opening_time = time(8, 0, 0)
        closing_time = time(22, 0, 0)

        return current_weekday in [0, 1,6] and opening_time <= current_time <= closing_time

def add_to_order(parameters:dict,session_id:str):
    
    food_items=parameters["food-items"]
    quantities = parameters["number"]

    if len(food_items) != len(quantities):
        fulfillment_text = "Sorry I didn't understand. Can you please specify food items and quantities clearly?"
    else:
        new_food_dict = dict(zip(food_items, quantities)) # it will create a dictionery

        if session_id in inprogress_orders: # if Session id exist
            current_food_dict = inprogress_orders[session_id]
            current_food_dict.update(new_food_dict)
            inprogress_orders[session_id] = current_food_dict
            confirmation = parameters.get("confirmation", "")
            if confirmation.lower() == "yes":
                del inprogress_orders[session_id]  # Delete the ongoing order
            else:
                fulfillment_text = "You have an ongoing order. Do you want to start a new order? (Please reply with 'yes' or 'no')"
                return JSONResponse(content={
                    "fulfillmentText": fulfillment_text
                })
        
        else:
            inprogress_orders[session_id] = new_food_dict

    order_str = generic_helper.get_str_from_food_dict(inprogress_orders[session_id])
    fulfillment_text = f"So far you have: {order_str}. Do you need anything else?"
    
    return JSONResponse(content={
        "fulfillmentText":fulfillment_text}) #track_order(parameters)


def welcome_intent(parameters:dict,session_id:str):
        return ""


def save_to_db(order: dict):
    next_order_id = db_helper.get_next_order_id()

    # Insert individual items along with quantity in orders table
    for food_item, quantity in order.items():
        rcode = db_helper.insert_order_item(
            food_item,
            quantity,
            next_order_id
        )

        if rcode == -1:
            return -1

    # Now insert order tracking status
    db_helper.insert_order_tracking(next_order_id, "in progress")

    return next_order_id

def complete_order(parameters: dict, session_id: str):
    if session_id not in inprogress_orders:
        fulfillment_text = "I'm having a trouble finding your order. Sorry! Can you place a new order please?"
    else:
        order = inprogress_orders[session_id]
        order_id = save_to_db(order)
        if order_id == -1:
            fulfillment_text = "Sorry, I couldn't process your order due to a backend error. " \
                               "Please place a new order again"
        else:
            order_total = db_helper.get_total_order_price(order_id)

            fulfillment_text = f"Awesome. We have placed your order. " \
                           f"Here is your order id # {order_id}. " \
                           f"Your order total is {order_total} which you can pay at the time of delivery!"

        del inprogress_orders[session_id]

    return JSONResponse(content={
        "fulfillmentText": fulfillment_text})


def track_order(parameters: dict,session_id: str):
    order_id = int(parameters['number'])
    order_status = db_helper.get_order_status(order_id)

    if order_status:
        fulfillment_text = f"The order status for order id: {order_id} is: {order_status}"
    else:
        fulfillment_text = f"No order found with order id: {order_id}"

    return JSONResponse(content={
        "fulfillmentText": fulfillment_text
    })


def remove_from_order(parameters: dict, session_id: str):
    if session_id not in inprogress_orders:
        return JSONResponse(content={
            "fulfillmentText": "I'm having a trouble finding your order. Sorry! Can you place a new order please?"
        })
    
    food_items = parameters["food-items"]
    current_order = inprogress_orders[session_id]

    removed_items = []
    no_such_items = []

    for item in food_items:
        if item not in current_order:
            no_such_items.append(item)
        else:
            removed_items.append(item)
            del current_order[item]

    if len(removed_items) > 0:
        fulfillment_text = f'Removed {",".join(removed_items)} from your order!'

    if len(no_such_items) > 0:
        fulfillment_text = f' Your current order does not have {",".join(no_such_items)}'

    if len(current_order.keys()) == 0:
        fulfillment_text += " Your order is empty!"
    else:
        order_str = generic_helper.get_str_from_food_dict(current_order)
        fulfillment_text += f" Here is what is left in your order: {order_str}"

    return JSONResponse(content={
        "fulfillmentText": fulfillment_text
    })





