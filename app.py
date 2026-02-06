import socket
import random
import string
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room as sio_join

app = Flask(__name__)
app.secret_key = 'battlepigs-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

rooms = {}
sid_to_room = {}

PIGS = [
    {'id': 1, 'name': 'Baozhu (Wifey Pig)', 'size': 5},
    {'id': 2, 'name': 'Zhubao (Hubby Pig)', 'size': 4},
    {'id': 3, 'name': 'White Pig', 'size': 3},
    {'id': 4, 'name': 'Black Pig', 'size': 3},
    {'id': 5, 'name': 'Xiao Zhu Tou (Baby)', 'size': 2},
]
EXPECTED = {p['id']: p['size'] for p in PIGS}


def gen_code():
    return ''.join(random.choices(string.ascii_uppercase, k=4))


def validate_board(board):
    counts = {}
    for r in range(10):
        for c in range(10):
            v = board[r][c]
            if v > 0:
                counts[v] = counts.get(v, 0) + 1
    return counts == EXPECTED


def get_player_idx(sid):
    code = sid_to_room.get(sid)
    if not code or code not in rooms:
        return None, None, None
    room = rooms[code]
    idx = next((i for i, p in enumerate(room['players']) if p['sid'] == sid), None)
    return code, room, idx


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('create_room')
def on_create(data):
    sid = request.sid
    name = data.get('name', '').strip()
    if not name:
        return emit('error', {'msg': 'Please enter your name'})
    code = gen_code()
    while code in rooms:
        code = gen_code()
    rooms[code] = {
        'players': [{'sid': sid, 'name': name, 'board': None, 'ready': False}],
        'shots': [set(), set()],
        'turn': 0,
        'phase': 'waiting',
        'winner': None,
    }
    sid_to_room[sid] = code
    sio_join(code)
    emit('room_created', {'code': code, 'name': name})


@socketio.on('join_game')
def on_join(data):
    sid = request.sid
    name = data.get('name', '').strip()
    code = data.get('code', '').strip().upper()
    if not name:
        return emit('error', {'msg': 'Please enter your name'})
    if code not in rooms:
        return emit('error', {'msg': 'Room not found'})
    room = rooms[code]
    if len(room['players']) >= 2:
        return emit('error', {'msg': 'Room is full'})
    room['players'].append({'sid': sid, 'name': name, 'board': None, 'ready': False})
    sid_to_room[sid] = code
    sio_join(code)
    room['phase'] = 'placing'
    p0, p1 = room['players'][0], room['players'][1]
    emit('game_start', {'you': 1, 'opponent': p0['name']})
    emit('game_start', {'you': 0, 'opponent': name}, to=p0['sid'])


@socketio.on('place_pigs')
def on_place(data):
    sid = request.sid
    code, room, idx = get_player_idx(sid)
    if idx is None:
        return
    board = data.get('board')
    if not board or not validate_board(board):
        return emit('error', {'msg': 'Invalid pig placement'})
    room['players'][idx]['board'] = board
    room['players'][idx]['ready'] = True
    opp = 1 - idx
    if len(room['players']) > 1 and room['players'][opp]['ready']:
        room['phase'] = 'playing'
        room['turn'] = 0
        emit('battle_start', {'turn': 0}, to=code)
    else:
        emit('wait_for_opponent', {})


@socketio.on('fire')
def on_fire(data):
    sid = request.sid
    code, room, idx = get_player_idx(sid)
    if idx is None or room['phase'] != 'playing' or room['turn'] != idx:
        return
    r, c = data.get('r'), data.get('c')
    if r is None or c is None or not (0 <= r < 10 and 0 <= c < 10):
        return
    if (r, c) in room['shots'][idx]:
        return
    room['shots'][idx].add((r, c))
    opp = 1 - idx
    opp_board = room['players'][opp]['board']
    cell = opp_board[r][c]
    hit = cell > 0
    sunk = None
    sunk_cells = None
    sunk_name = None
    if hit:
        pig_id = cell
        cells = [(ri, ci) for ri in range(10) for ci in range(10) if opp_board[ri][ci] == pig_id]
        if all((ri, ci) in room['shots'][idx] for ri, ci in cells):
            sunk = pig_id
            sunk_cells = cells
            sunk_name = next(p['name'] for p in PIGS if p['id'] == pig_id)
    game_over = False
    if hit:
        all_cells = [(ri, ci) for ri in range(10) for ci in range(10) if opp_board[ri][ci] > 0]
        if all((ri, ci) in room['shots'][idx] for ri, ci in all_cells):
            game_over = True
            room['phase'] = 'finished'
            room['winner'] = idx
    if not game_over:
        room['turn'] = opp
    emit('fire_result', {
        'r': r, 'c': c, 'hit': hit, 'shooter': idx, 'turn': room['turn'],
        'sunk': sunk, 'sunk_cells': sunk_cells, 'sunk_name': sunk_name,
        'game_over': game_over,
        'winner_name': room['players'][idx]['name'] if game_over else None,
    }, to=code)


@socketio.on('play_again')
def on_play_again():
    sid = request.sid
    code, room, idx = get_player_idx(sid)
    if idx is None:
        return
    for p in room['players']:
        p['board'] = None
        p['ready'] = False
    room['shots'] = [set(), set()]
    room['turn'] = 0
    room['phase'] = 'placing'
    room['winner'] = None
    for i, p in enumerate(room['players']):
        emit('game_start', {
            'you': i, 'opponent': room['players'][1 - i]['name']
        }, to=p['sid'])


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    code = sid_to_room.pop(sid, None)
    if code and code in rooms:
        room = rooms[code]
        left = next((p for p in room['players'] if p['sid'] == sid), None)
        emit('opponent_left', {'name': left['name'] if left else '?'}, to=code)
        for p in room['players']:
            sid_to_room.pop(p['sid'], None)
        del rooms[code]


if __name__ == '__main__':
    ip = socket.gethostbyname(socket.gethostname())
    print(f"\n  Battle Pigs is running!")
    print(f"  --> http://{ip}:8080\n")
    socketio.run(app, host='0.0.0.0', port=8080, allow_unsafe_werkzeug=True)
