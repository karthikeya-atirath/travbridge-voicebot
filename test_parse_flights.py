import json

with open("apidata.txt", "r") as f:
    data = json.load(f)

source = data.get("data", {})
flight_options = source.get("flightOptions", {})

flights = []
if flight_options:
    for direction in ["onward", "return"]:
        if direction in flight_options:
            opts = flight_options[direction].get("options", [])
            if opts:
                # take first recommended option or first option
                best_opt = next((o for o in opts if o.get("recommended")), opts[0])
                flights.append({
                    "flightNo": best_opt.get("flightNumber"),
                    "airline": best_opt.get("airlineName"),
                    "departureCity": best_opt.get("departure", {}).get("cityName"),
                    "arrivalCity": best_opt.get("arrival", {}).get("cityName"),
                    "duration": best_opt.get("duration"),
                    "direction": direction
                })

print(json.dumps(flights, indent=2))
