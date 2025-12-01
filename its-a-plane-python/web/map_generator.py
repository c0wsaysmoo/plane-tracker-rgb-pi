import folium
import os
import math
from config import LOCATION_HOME, DISTANCE_UNITS


WEB_DIR = os.path.dirname(__file__)
MAPS_DIR = os.path.join(WEB_DIR, "static", "maps")
os.makedirs(MAPS_DIR, exist_ok=True)

def get_unit_label():
    return "mi" if DISTANCE_UNITS.lower() == "imperial" else "km"
    
def great_circle_points(start, end, steps=50):
    lat1, lon1 = map(math.radians, start)
    lat2, lon2 = map(math.radians, end)

    d = 2 * math.asin(math.sqrt(
        math.sin((lat2 - lat1)/2)**2 +
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1)/2)**2
    ))

    if d == 0:
        return [start, end]

    points = []
    for i in range(steps + 1):
        f = i / steps
        A = math.sin((1 - f) * d) / math.sin(d)
        B = math.sin(f * d) / math.sin(d)

        x = A * math.cos(lat1) * math.cos(lon1) + B * math.cos(lat2) * math.cos(lon2)
        y = A * math.cos(lat1) * math.sin(lon1) + B * math.cos(lat2) * math.sin(lon2)
        z = A * math.sin(lat1) + B * math.sin(lat2)

        lat = math.atan2(z, math.sqrt(x*x + y*y))
        lon = math.atan2(y, x)

        points.append([math.degrees(lat), math.degrees(lon)])

    return points

def normalize_longitudes(points):
    fixed = [points[0]]
    for lat, lon in points[1:]:
        prev_lon = fixed[-1][1]
        if lon - prev_lon > 180:
            lon -= 360
        elif lon - prev_lon < -180:
            lon += 360
        fixed.append([lat, lon])
    return fixed

def align_to_reference_tile(lon, ref_lon):
    while lon - ref_lon > 180:
        lon -= 360
    while lon - ref_lon < -180:
        lon += 360
    return lon
    
def generate_closest_map(entries, filename="closest.html"):
    m = folium.Map(location=LOCATION_HOME[:2], zoom_start=10)
    unit_label = get_unit_label()
    colors = ["red","blue","green","purple","pink","darkred","darkblue","darkgreen","cadetblue","brown"]

    # Home marker
    folium.Marker(
        LOCATION_HOME[:2],
        popup="Home",
        icon=folium.Icon(color="orange")
    ).add_to(m)

    all_locs = [LOCATION_HOME[:2]]

    for idx, entry in enumerate(entries):
        color = colors[idx % len(colors)]
        plane_loc = [entry["plane_latitude"], entry["plane_longitude"]]
        dist_origin = entry.get("distance_origin", 0)
        dist_dest = entry.get("distance_destination", 0)
        dist_home = entry.get("distance", 0)

        popup_text = f"""
        <div style="
            font-size:14px;
            font-family: Arial, sans-serif;
            line-height:1.4;
            white-space: nowrap;
        ">
            <b>Timestamp:</b> {entry.get('timestamp','')}<br>
            <b>Flight:</b> {entry.get('callsign','Plane')}<br>
            <b>From:</b> {entry.get('origin','')}<br>
            <b>To:</b> {entry.get('destination','')}<br>
            <b>Plane:</b> {entry.get('plane','')}<br>
            <b>Distance to Home:</b> {dist_home:.2f} {unit_label}<br>
            <b>Distance Origin:</b> {dist_origin:.2f} {unit_label}<br>
            <b>Distance Destination:</b> {dist_dest:.2f} {unit_label}
        </div>
        """
        folium.Marker(
            plane_loc,
            popup=popup_text,
            icon=folium.Icon(color=color)
        ).add_to(m)

        all_locs.append(plane_loc)

    # Fit map to all locations (home + all planes)
    m.fit_bounds(all_locs)

    # Save map
    filepath = os.path.join(MAPS_DIR, filename)
    m.save(filepath)
    return filepath

def generate_farthest_map(entries, filename="farthest.html"):
    unit_label = get_unit_label()
    colors = ["red","blue","green","purple","pink","darkred","darkblue","darkgreen","cadetblue","brown"]

    # Initialize map centered on home
    m = folium.Map(location=LOCATION_HOME[:2], zoom_start=4)

    # Add home marker
    folium.Marker(
        LOCATION_HOME[:2],
        popup="Home",
        icon=folium.Icon(color="orange")
    ).add_to(m)

    all_locs = [LOCATION_HOME[:2]]
    ref_lon = None  # reference tile longitude

    for idx, entry in enumerate(entries):
        color = colors[idx % len(colors)]

        # Original coordinates
        plane_lat, plane_lon = entry["plane_latitude"], entry["plane_longitude"]
        origin_lat, origin_lon = entry["origin_latitude"], entry["origin_longitude"]
        dest_lat, dest_lon = entry["destination_latitude"], entry["destination_longitude"]

        # Use the first flight's plane longitude as reference
        if ref_lon is None:
            ref_lon = plane_lon

        # Align all points to the reference tile
        plane_lon = align_to_reference_tile(plane_lon, ref_lon)
        origin_lon = align_to_reference_tile(origin_lon, ref_lon)
        dest_lon = align_to_reference_tile(dest_lon, ref_lon)

        dist_origin = entry.get("distance_origin", 0)
        dist_dest = entry.get("distance_destination", 0)
        dist_home = entry.get("distance", 0)

        # Plane popup
        plane_popup = f"""
        <div style="font-size:14px; font-family:Arial; line-height:1.4; white-space:nowrap;">
            <b>Timestamp:</b> {entry.get('timestamp','')}<br>
            <b>Flight:</b> {entry.get('callsign','Plane')}<br>
            <b>From:</b> {entry.get('origin','')}<br>
            <b>To:</b> {entry.get('destination','')}<br>
            <b>Plane:</b> {entry.get('plane','')}<br>
            <b>Distance to Home:</b> {dist_home:.2f} {unit_label}<br>
            <b>Distance Origin:</b> {dist_origin:.2f} {unit_label}<br>
            <b>Distance Destination:</b> {dist_dest:.2f} {unit_label}
        </div>
        """

        # Add markers
        folium.Marker([plane_lat, plane_lon], popup=plane_popup, icon=folium.Icon(color=color)).add_to(m)
        folium.CircleMarker([origin_lat, origin_lon], radius=5, color=color, fill=True, fill_color=color,
                            popup=f"{entry.get('origin','UNK')} Airport\nDistance: {dist_origin:.2f} {unit_label}").add_to(m)
        folium.CircleMarker([dest_lat, dest_lon], radius=5, color=color, fill=True, fill_color=color,
                            popup=f"{entry.get('destination','UNK')} Airport\nDistance: {dist_dest:.2f} {unit_label}").add_to(m)

        # Great-circle paths
        gc1 = great_circle_points([origin_lat, entry["origin_longitude"]], [plane_lat, plane_lon])
        gc1 = [[lat, align_to_reference_tile(lon, ref_lon)] for lat, lon in gc1]
        folium.PolyLine(gc1, color=color, weight=2, opacity=0.9,
                tooltip=f"Path: {entry.get('origin','UNK')} to Current").add_to(m)

        gc2 = great_circle_points([plane_lat, plane_lon], [dest_lat, entry["destination_longitude"]])
        gc2 = [[lat, align_to_reference_tile(lon, ref_lon)] for lat, lon in gc2]
        folium.PolyLine(gc2, color=color, weight=2, opacity=0.9, dash_array="5,5",
                tooltip=f"Path: Current to {entry.get('destination','UNK')}").add_to(m)

        all_locs.extend([[plane_lat, plane_lon], [origin_lat, origin_lon], [dest_lat, dest_lon]])

    # Fit map to all locations
    m.fit_bounds(all_locs)

    # Save map
    filepath = os.path.join(MAPS_DIR, filename)
    m.save(filepath)
    return filepath

