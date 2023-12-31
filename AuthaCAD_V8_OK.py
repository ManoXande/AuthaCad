import math
import pyautocad
import logging
import re
from pyautocad.cache import Cached
from jinja2 import Template
from num2words import num2words

def are_floats_equal(f1, f2, tolerance=1e-9):
    return abs(f1 - f2) < tolerance

def is_point_close(point1, point2, tolerance=1e-6):
    return math.sqrt((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2) < tolerance

def extract_coordinates(entity):
    try:
        cached_entity = Cached(entity)
        if cached_entity.EntityName == 'AcDbPolyline':
            coords = [round(coord, 3) for coord in cached_entity.Coordinates]
            vertices_groups = [(coords[i], coords[i+1]) for i in range(0, len(coords), 2)]
            return {'Type': 'Polyline', 'Coordinates': vertices_groups}
        elif cached_entity.EntityName == 'AeccDbCogoPoint':
            properties = ['Easting', 'Northing', 'Elevation', 'RawDescription', 'Number']
            extracted_properties = {prop: round(float(getattr(cached_entity, prop)), 3) if isinstance(getattr(cached_entity, prop), float) else getattr(cached_entity, prop) for prop in properties}
            return {'Type': 'CogoPoint', **extracted_properties}
        elif cached_entity.EntityName == 'AcDbText' or cached_entity.EntityName == 'AcDbMText':
            properties = ['InsertionPoint', 'TextString']
            extracted_properties = {prop: getattr(cached_entity, prop) for prop in properties}
            extracted_properties['InsertionPoint'] = tuple(round(coord, 3) for coord in extracted_properties['InsertionPoint'][:2])
            return {'Type': 'Text', **extracted_properties}
        else:
            logging.warning(f"Unsupported entity type: {cached_entity.EntityName} for entity: {cached_entity}")
            return None
    except Exception as e:
        logging.error(f"Error extracting coordinates: {e} for entity: {cached_entity}")
        raise e

def is_point_in_polygon(point, polygon):
    x, y = point
    polygon = list(zip(polygon[::2], polygon[1::2]))  # Create pairs of coordinates
    n = len(polygon)
    inside = False

    p1x, p1y = polygon[0]
    for i in range(n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside

def get_center(vertices):
    x_coords = [vertices[i] for i in range(0, len(vertices), 2)]
    y_coords = [vertices[i] for i in range(1, len(vertices), 2)]
    return sum(x_coords) / len(x_coords), sum(y_coords) / len(y_coords)

def calculate_distance(point1, point2):
    return math.sqrt((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2)

def get_text_inside_polyline(entity, selection_set):
    if entity.EntityName != 'AcDbPolyline':
        return None, None

    vertices = entity.Coordinates
    center = get_center(vertices)

    inner_texts = []
    min_distance = float('inf')
    nearest_text = ""
    nearest_text_unclean = ""

    for inner_entity in selection_set:
        if inner_entity.EntityName in ['AcDbText', 'AcDbMText']:
            text_coords = inner_entity.InsertionPoint[:2]
            if is_point_in_polygon(text_coords, vertices):
                text_distance = calculate_distance(center, text_coords)
                inner_texts.append((text_distance, re.sub(r'\\P', '', inner_entity.TextString)))
                if text_distance < min_distance:
                    min_distance = text_distance
                    nearest_text = re.sub(r'\\P', '', inner_entity.TextString)
                    nearest_text_unclean = inner_entity.TextString

    return (re.sub(r'\\pxqc;', '', nearest_text) if nearest_text else "No text found", nearest_text_unclean)

def get_all_entity_names(selection_set):
    """Return a set of all entity names in the selection set."""
    entity_names = set()
    for entity in selection_set:
        try:
            entity_names.add(entity.EntityName)
        except AttributeError as e:
            print(f"Entity of type {type(entity)} does not have an attribute 'EntityName'.")
            raise e
    return entity_names

def get_vertex_name(entity, selection_set, vertex, tolerance=1e-6):
    all_entity_names = get_all_entity_names(selection_set)
    cogopoint_names = {name for name in all_entity_names if 'cogopoint' in name.lower()}

    for inner_entity in selection_set:
        if inner_entity.EntityName in cogopoint_names:
            cogo_point_coords = (inner_entity.Easting, inner_entity.Northing)
            if is_point_close(cogo_point_coords, vertex, tolerance):
                point_number = inner_entity.Number
                point_elevation = inner_entity.Elevation
                return f"V{point_number}", point_elevation
    print(f"No matching CogoPoint found for vertex: {vertex}")
    return "Vertex not found", None

acad = pyautocad.Autocad(create_if_not_exists=True)
acad.prompt("Select entities\n")

selectionSetName = 'SS1'
selection_set = None
for i in range(acad.ActiveDocument.SelectionSets.Count):
    if acad.ActiveDocument.SelectionSets.Item(i).Name == selectionSetName:
        selection_set = acad.ActiveDocument.SelectionSets.Item(i)
        break

if selection_set is None:
    selection_set = acad.ActiveDocument.SelectionSets.Add(selectionSetName)
else:
    selection_set.Clear()

selection_set.SelectOnScreen()

template_initial = Template("Inicia-se a descrição deste perímetro no vértice {{ current_vertex_name }}, georreferenciado no Sistema Geodésico Brasileiro, DATUM - SIRGAS2000, MC-51°W, de coordenadas N {{ current_vertex[1] }}m e E {{ current_vertex[0] }}m de altitude {{ current_vertex_elevation }}m; deste segue confrontando com {{ adjacent_text }}, com azimute de {{ degrees }}°{{ minutes }}'{{ seconds }}\" por uma distância de {{ distance }}m até o vértice {{ next_vertex_name }}, de coordenadas N {{ next_vertex[1] }}m e E {{ next_vertex[0] }}m de altitude {{ next_vertex_elevation }}m;")
template_other = Template("Deste segue confrontando com {{ adjacent_text }}, com azimute de {{ degrees }}°{{ minutes }}'{{ seconds }}\" por uma distância de {{ distance }}m até o vértice {{ next_vertex_name }}, de coordenadas N {{ next_vertex[1] }}m e E {{ next_vertex[0] }}m de altitude {{ next_vertex_elevation }}m;")
template_final = Template("Todas as coordenadas aqui descritas estão georreferenciadas ao Sistema Geodésico Brasileiro e encontram-se representadas no Sistema UTM, referenciadas ao Meridiano Central nº 51 WGr, tendo como Datum o SIRGAS2000. Todos os azimutes e distâncias, área e perímetro foram calculados no plano de projeção UTM.")
template_header = Template("{{ lot_number }} da QUADRA “XX”, com área de {{ area }}m² ({{ area_text }}), com a seguinte descrição:")
template_table = Template("Lado {{ current_vertex_name }}->{{ next_vertex_name }}: {{ current_vertex_name }}({{ current_vertex[0] }}, {{ current_vertex[1] }}, {{ current_vertex_elevation }}) -> {{ next_vertex_name }}({{ next_vertex[0] }}, {{ next_vertex[1] }}, {{ next_vertex_elevation }}), Distância: {{ distance }} m, Azimute: {{ degrees }}°{{ minutes }}'{{ seconds }}\"; ")
 
def generate_text_from_polyline(entity, selection_set, text_inside, text_inside_unclean):
    entity_info = extract_coordinates(entity)
    if entity_info is None or entity_info['Type'] != 'Polyline':
        return None

    vertices = entity_info['Coordinates']
    area = round(entity.Area, 2)  # convert to m²
    perimeter = round(entity.Length, 2)  # convert to m

    lot_number = re.search(r'Lote nº (\d+)', text_inside_unclean)  # extract from uncleaned text
    lot_number = lot_number.group(1) if lot_number else text_inside  # replace 'XX' with text_inside if not found

    quad_number = re.search(r'Quadra (\w+)', text_inside_unclean)
    quad_number = quad_number.group(1) if quad_number else text_inside  # replace 'XX' with text_inside if not found
    area_text = num2words(area, lang='pt_BR')

    # Replace "LOTE Nº XX" with the nearest text inside the polyline
    header_text = template_header.render(lot_number=lot_number, quad_number=quad_number, area=area, area_text=area_text)
    
    descriptions = []


    for i in range(len(vertices)):
        current_vertex = vertices[i]
        next_vertex = vertices[(i+1) % len(vertices)]

        tolerance = 0.001  

        current_vertex_name, current_vertex_elevation = get_vertex_name(entity, selection_set, current_vertex, tolerance)
        next_vertex_name, next_vertex_elevation = get_vertex_name(entity, selection_set, next_vertex, tolerance)

        if are_floats_equal(current_vertex[0], next_vertex[0]) and are_floats_equal(current_vertex[1], next_vertex[1]):
            continue

        distance = round(math.sqrt((next_vertex[0] - current_vertex[0]) ** 2 + (next_vertex[1] - current_vertex[1]) ** 2), 2)

        delta_x = next_vertex[0] - current_vertex[0]
        delta_y = next_vertex[1] - current_vertex[1]
        azimuth_rad = math.atan2(delta_x, delta_y)
        azimuth_deg = math.degrees(azimuth_rad)
        if azimuth_deg < 0:
            azimuth_deg += 360

        degrees = int(azimuth_deg)
        minutes = int((azimuth_deg - degrees) * 60)
        seconds = round(((azimuth_deg - degrees) * 60 - minutes) * 60, 2)

        adjacent_polyline = None
        for other_entity in selection_set:
            if other_entity.Handle == entity.Handle:
                continue
            other_info = extract_coordinates(other_entity)
            if other_info is not None and other_info['Type'] == 'Polyline':
                other_vertices = other_info['Coordinates']

                if any(are_floats_equal(v[0], current_vertex[0]) and are_floats_equal(v[1], current_vertex[1]) for v in other_vertices) and \
                   any(are_floats_equal(v[0], next_vertex[0]) and are_floats_equal(v[1], next_vertex[1]) for v in other_vertices):
                    adjacent_polyline = other_entity
                    break

        adjacent_text = "No confrontante found"
        if adjacent_polyline:
            adjacent_text = get_text_inside_polyline(adjacent_polyline, selection_set)[0]

        seconds_str = f"{seconds:.2f}"

        # Render the description
        if i == 0:
            description = template_initial.render(current_vertex_name=current_vertex_name, current_vertex=current_vertex, current_vertex_elevation=current_vertex_elevation, adjacent_text=adjacent_text, degrees=degrees, minutes=minutes, seconds=seconds_str, distance=distance, next_vertex_name=next_vertex_name, next_vertex=next_vertex, next_vertex_elevation=next_vertex_elevation)
        else:
            description = template_other.render(current_vertex_name=current_vertex_name, current_vertex=current_vertex, current_vertex_elevation=current_vertex_elevation, adjacent_text=adjacent_text, degrees=degrees, minutes=minutes, seconds=seconds_str, distance=distance, next_vertex_name=next_vertex_name, next_vertex=next_vertex, next_vertex_elevation=next_vertex_elevation)

        descriptions.append(description)

        # Render the table line
        table_line = template_table.render(current_vertex_name=current_vertex_name, current_vertex=current_vertex, current_vertex_elevation=current_vertex_elevation, adjacent_text=adjacent_text, degrees=degrees, minutes=minutes, seconds=seconds_str, distance=distance, next_vertex_name=next_vertex_name, next_vertex=next_vertex, next_vertex_elevation=next_vertex_elevation)
        print(table_line)
        
        
    descriptions_text = '\n'.join(descriptions)
    final_text = template_final.render()

    # Print old formatted text
    print(f"{header_text}\n\n{descriptions_text}\n{final_text}\n")

# Use the function for each polyline in the selection set
for entity in selection_set:
    if entity.EntityName == 'AcDbPolyline':
        text_inside, text_inside_unclean = get_text_inside_polyline(entity, selection_set)
        generate_text_from_polyline(entity, selection_set, text_inside, text_inside_unclean)
