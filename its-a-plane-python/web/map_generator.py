import folium
import os
from config import LOCATION_HOME, DISTANCE_UNITS


WEB_DIR = os.path.dirname(__file__)
MAPS_DIR = os.path.join(WEB_DIR, "static", "maps")
os.makedirs(MAPS_DIR, exist_ok=True)

def get_unit_label():
    return "mi" if DISTANCE_UNITS.lower() == "imperial" else "km"
    
def hsl_color(index, total):
    """
    Generate a distinct, evenly spaced color using HSL.
    Output is CSS format: 'hsl(###, 80%, 45%)'
    """
    hue = int((index / max(total, 1)) * 360)
    return f"hsl({hue}, 80%, 45%)"

def generate_closest_map(entries, filename="closest.html"):
    m = folium.Map(location=LOCATION_HOME[:2], zoom_start=10)
    unit_label = get_unit_label()
    colors = ["red","blue","green","purple","pink","darkred","darkblue","darkgreen","cadetblue","orange"]

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
            <b>Distance to Home:</b> {dist_home:.2f} {unit_label}
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
    m = folium.Map(location=LOCATION_HOME[:2], zoom_start=4)
    unit_label = get_unit_label()
    colors = ["red","blue","green","purple","pink","darkred","darkblue","darkgreen","cadetblue","orange"]

    # Home pin stays a standard folium icon
    folium.Marker(
        LOCATION_HOME[:2],
        popup="Home",
        icon=folium.Icon(color="orange")
    ).add_to(m)

    all_locs = [LOCATION_HOME[:2]]

    for idx, entry in enumerate(entries):
        color = colors[idx % len(colors)]

        plane_loc = [entry["plane_latitude"], entry["plane_longitude"]]
        origin_loc = [entry["origin_latitude"], entry["origin_longitude"]]
        dest_loc = [entry["destination_latitude"], entry["destination_longitude"]]

        dist_origin = entry.get("distance_origin", 0)
        dist_dest = entry.get("distance_destination", 0)
        dist_home = entry.get("distance", 0)

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

        # Plane marker 
        folium.Marker(
            plane_loc,
            popup=plane_popup,
            icon=folium.Icon(color=color)
        ).add_to(m)

        all_locs.append(plane_loc)

        # Polyline path colored with the same color
        folium.PolyLine(
            [origin_loc, plane_loc, dest_loc],
            color=color,
            weight=2,
            opacity=0.9
        ).add_to(m)

        # Origin + destination markers
        folium.CircleMarker(
            origin_loc,
            radius=5,
            color=color,
            fill=True,
            fill_color=color,
            popup=f"{entry.get('origin','UNK')} Airport\nDistance: {dist_origin:.2f} {unit_label}"
        ).add_to(m)

        folium.CircleMarker(
            dest_loc,
            radius=5,
            color=color,
            fill=True,
            fill_color=color,
            popup=f"{entry.get('destination','UNK')} Airport\nDistance: {dist_dest:.2f} {unit_label}"
        ).add_to(m)

        all_locs.extend([origin_loc, dest_loc])

    m.fit_bounds(all_locs)
    filepath = os.path.join(MAPS_DIR, filename)
    m.save(filepath)
    return filepath
