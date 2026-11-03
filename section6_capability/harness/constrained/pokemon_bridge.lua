local function trailing_slash(path)
    if string.sub(path, -1) == "\\" then return path end
    return path .. "\\"
end
local SAVE_BASE = trailing_slash(os.getenv("POKEMON_EVAL_LUA_SAVE_BASE")
    or error("POKEMON_EVAL_LUA_SAVE_BASE is not set; the harness sets it per attempt"))
local BRIDGE_DIR = trailing_slash(os.getenv("POKEMON_EVAL_BRIDGE_DIR") or (SAVE_BASE .. "_bridge"))
local STATE_FILE = BRIDGE_DIR .. "state.json"
local STATE_TMP  = BRIDGE_DIR .. "state.tmp"
local CMD_FILE   = BRIDGE_DIR .. "cmd.txt"
local TEXT_FILE  = BRIDGE_DIR .. "text.txt"
local INPUT_LOG_FILE = BRIDGE_DIR .. "input_log.jsonl"

local original_joypad_set = joypad.set
local input_frame_counter = 0

joypad.set = function(player, buttons)
    local f = io.open(INPUT_LOG_FILE, "a")
    if f then
        local parts = {}
        for k, v in pairs(buttons) do
            if v then table.insert(parts, '"' .. k .. '":true') end
        end
        if #parts > 0 then
            f:write('{"f":' .. input_frame_counter .. ',"b":{' .. table.concat(parts, ',') .. '}}\n')
        end
        f:close()
    end
    return original_joypad_set(player, buttons)
end
local POKEMON_ACCESS_DIR = trailing_slash(os.getenv("POKEMON_EVAL_POKEMON_ACCESS")
    or error("POKEMON_EVAL_POKEMON_ACCESS is not set; the harness sets it per attempt"))

os.execute('mkdir "' .. BRIDGE_DIR .. '" 2>nul')

local tolk_lines = {}

local fake_tolk = {
    output = function(s)
        if s == nil or s == "" then return end
        table.insert(tolk_lines, tostring(s))
        local f = io.open(TEXT_FILE, "a")
        if f then
            f:write(tostring(s) .. "\n")
            f:close()
        end
    end,
    silence = function() end,
}
package.loaded["tolk"] = fake_tolk

audio = {
    play = function() return 0 end,
    stop = function() end,
    pitch = function() end,
}

local pending_inputbox_response = nil
local pending_combobox_response = nil

local fake_controls = {
    inputbox = function(title, caption, default)
        return nil
    end,
    combobox = function(title, caption, list, data)
        return nil
    end,
}
package.loaded["win-controls"] = fake_controls


package.path = POKEMON_ACCESS_DIR .. "?.lua;" .. POKEMON_ACCESS_DIR .. "?\\init.lua;" .. package.path

local pf = io.open(POKEMON_ACCESS_DIR .. "pokemon.lua", "r")
if not pf then
    error("Cannot open " .. POKEMON_ACCESS_DIR .. "pokemon.lua")
end
local source = pf:read("*a")
pf:close()

local cut_pos = source:find('\ntolk = require "tolk"')
if cut_pos then
    source = source:sub(1, cut_pos - 1)
end

local fn, err = loadstring(source, "@" .. POKEMON_ACCESS_DIR .. "pokemon.lua")
if not fn then
    error("Failed to load pokemon.lua: " .. tostring(err))
end
fn()

scriptpath = POKEMON_ACCESS_DIR

tolk = fake_tolk

init_script()

memory.registerexec(0x100, init_script)
memory.registerexec(0x8000000, init_script)

local auto_walk_path = nil   -- raw node list from find_path_to_xy
local auto_walk_index = 0    -- current node index
local auto_walk_active = false
local auto_walk_map_id = nil -- map ID when walk started (cancel on change)
local auto_walk_stuck = 0    -- frames position unchanged (stuck detection)
local auto_walk_prev_x = nil -- last known x
local auto_walk_prev_y = nil -- last known y

local function get_hold_frames()
    local id = emu.platform()
    if id == 0 then return 4 end  -- GBA
    return 8                       -- GB/GBC
end

local button_queue = {}  -- {button, frames_remaining}
local PRESS_FRAMES = nil -- set on first frame

local frame_counter = 0
local last_state_json = ""
local EXPORT_INTERVAL = 5   -- export every N frames (lower = less display lag)

local function write_atomic(path, tmp_path, content)
    local f = io.open(tmp_path, "w")
    if f then
        f:write(content)
        f:close()
        os.remove(path)
        os.rename(tmp_path, path)
    end
end

local function json_escape(s)
    if s == nil then return "null" end
    s = tostring(s)
    s = s:gsub('\\', '\\\\')
    s = s:gsub('"', '\\"')
    s = s:gsub('\n', '\\n')
    s = s:gsub('\r', '\\r')
    s = s:gsub('\t', '\\t')
    return '"' .. s .. '"'
end

local function json_encode(val)
    if val == nil then return "null" end
    local t = type(val)
    if t == "boolean" then
        return val and "true" or "false"
    elseif t == "number" then
        if val ~= val then return "null" end  -- NaN
        if val == math.huge or val == -math.huge then return "null" end
        return tostring(val)
    elseif t == "string" then
        return json_escape(val)
    elseif t == "table" then
        local n = #val
        local is_array = true
        local count = 0
        for _ in pairs(val) do count = count + 1 end
        if count ~= n then
            is_array = false
        end
        if count == 0 then is_array = true end
        if is_array then
            local parts = {}
            for i = 1, n do
                table.insert(parts, json_encode(val[i]))
            end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            for k, v in pairs(val) do
                table.insert(parts, json_escape(tostring(k)) .. ":" .. json_encode(v))
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    end
    return "null"
end

local function extract_menu_items()
    if screen == nil or screen.menu_position == nil then
        return nil
    end

    local ok_kb, is_kb = pcall(function() return screen:keyboard_showing() end)
    if ok_kb and is_kb then return nil end

    local ok_hdr, results = pcall(generate_menu_header)
    if not ok_hdr or not results then return nil end

    local startpos = results.start_x
    local endpos = results.end_x
    if results.has_left_border then
        startpos = startpos + 1
    end
    if results.has_right_border then
        endpos = endpos - 1
    end

    if screen.tile_lines[3] and screen.tile_lines[3]:match(HEALTH_BAR) then
        return nil
    end

    local function force_translate(tileline)
        local l = ""
        for i = 1, #tileline do
            local byte = tileline:sub(i, i):byte()
            if byte ~= 0xed and byte ~= 0xeb then
                l = l .. translate(byte)
            end
        end
        return l
    end

    local items = {}
    local selected = -1
    local cursor_line = screen.menu_position[LINE]

    for line_idx = results.start_y + 1, results.end_y - 1 do
        if screen.tile_lines[line_idx] then
            local tile_line = get_menu_item(screen.tile_lines[line_idx], startpos, endpos)
            local text = force_translate(tile_line)
            text = text:match("^%s*(.-)%s*$") or ""
            if text ~= "" then
                if line_idx == cursor_line then
                    selected = #items  -- 0-based index
                end
                table.insert(items, text)
            end
        end
    end

    if #items == 0 then return nil end
    return {items = items, selected = selected}
end

local function extract_battle_menu()
    if screen == nil or screen.menu_position == nil then
        return nil
    end

    if not screen.tile_lines[3] or not screen.tile_lines[3]:match(HEALTH_BAR) then
        return nil
    end

    local ok_hdr, results = pcall(generate_menu_header)
    if not ok_hdr or not results then return nil end

    local correctpos = nil
    if screen.tile_lines[15] and screen.tile_lines[15]:match("\xe1\xe2\x7f") then
        correctpos = screen.tile_lines[15]:find("\xe1\xe2\x7f") - 1
    elseif screen.tile_lines[15] and screen.tile_lines[15]:match("\xf1") then
        correctpos = 14
    end

    if correctpos == nil then return nil end

    local startpos = results.start_x
    local endpos = results.end_x
    if results.has_left_border then startpos = startpos + 1 end
    if results.has_right_border then endpos = endpos - 1 end

    local function ft(tileline)
        local l = ""
        for i = 1, #tileline do
            local byte = tileline:sub(i, i):byte()
            if byte ~= 0xed and byte ~= 0xeb then
                l = l .. translate(byte)
            end
        end
        return l
    end

    local rows = {}
    for line_idx = results.start_y + 1, results.end_y - 1 do
        if screen.tile_lines[line_idx] then
            local left_tile = get_menu_item(screen.tile_lines[line_idx], startpos, correctpos)
            local left_text = ft(left_tile):match("^%s*(.-)%s*$") or ""
            local right_tile = get_menu_item(screen.tile_lines[line_idx], correctpos, endpos)
            local right_text = ft(right_tile):match("^%s*(.-)%s*$") or ""

            if left_text ~= "" or right_text ~= "" then
                table.insert(rows, {left_text, right_text})
            end
        end
    end

    if #rows == 0 then return nil end

    local items = {}
    for _, pair in ipairs(rows) do
        if pair[1] ~= "" then table.insert(items, pair[1]) end
        if pair[2] ~= "" then table.insert(items, pair[2]) end
    end

    local selected = -1
    local cursor_line = screen.menu_position[LINE]
    local cursor_col = screen.menu_position[COLUMN]
    for i, _ in ipairs(rows) do
        local row_line = results.start_y + i
        if cursor_line == row_line then
            if cursor_col < correctpos then
                selected = (i - 1) * 2     -- left column
            else
                selected = (i - 1) * 2 + 1 -- right column
            end
        end
    end

    return {items = items, selected = selected}
end

local function build_state()
    local state = {ready = true}

    local ok_pokedex, pokedex_events = pcall(memory.readbyte, 0xD74B)
    state.has_pokedex = ok_pokedex and bit.band(pokedex_events, 0x20) ~= 0 or false

    local ok_pos, px, py = pcall(get_player_xy)
    if ok_pos then
        state.player_x = px
        state.player_y = py
    else
        state.player_x = -1
        state.player_y = -1
    end

    local ok_map, map_id = pcall(get_map_id)
    if ok_map then
        state.map_id = map_id
        local ok_name, map_name = pcall(get_map_name, map_id)
        state.map_name = ok_name and map_name or ("Map " .. map_id)
    else
        state.map_id = 0
        state.map_name = "Unknown"
    end

    local ok_ts, tileset_id = pcall(memory.readbyte, 0xD367)
    state.tileset_id = ok_ts and tileset_id or -1

    local ok_m, m1 = pcall(memory.readbyte, 0xD347)
    local ok_m2, m2 = pcall(memory.readbyte, 0xD348)
    local ok_m3, m3 = pcall(memory.readbyte, 0xD349)
    if ok_m and ok_m2 and ok_m3 then
        state.player_money = bit.rshift(m1, 4) * 100000 + bit.band(m1, 0xF) * 10000
                           + bit.rshift(m2, 4) * 1000 + bit.band(m2, 0xF) * 100
                           + bit.rshift(m3, 4) * 10 + bit.band(m3, 0xF)
    end

    local ok_tm, _ = pcall(function()
        local tm = {}
        for i = 0, 359 do
            tm[i + 1] = memory.readbyte(0xC3A0 + i)
        end
        state.tile_map = tm
    end)
    if not ok_tm then
        state.tile_map = {}
    end

    local ok_battle, is_in_battle = pcall(in_battle)
    is_in_battle = ok_battle and is_in_battle

    local ok_text, textbox = pcall(get_textbox)
    local text_lines = {}
    if ok_text and textbox then
        for _, line in ipairs(textbox) do
            local trimmed = line
            if type(trimmed) == "string" then
                trimmed = trimmed:match("^%s*(.-)%s*$") or ""
                if trimmed ~= "" then
                    table.insert(text_lines, trimmed)
                end
            end
        end
    end
    state.text_lines = text_lines

    local ok_party, _ = pcall(function()
        local count = memory.readbyte(0xD163)
        if count > 0 and count <= 6 then
            local party = {}
            for i = 0, count - 1 do
                local base = 0xD16B + i * 0x2C
                local nick_base = 0xD2B5 + i * 11
                local name_bytes = {}
                for j = 0, 10 do
                    local b = memory.readbyte(nick_base + j)
                    if b == 0x50 then break end
                    table.insert(name_bytes, b)
                end
                local level = memory.readbyte(base + 0x21)
                local hp = memory.readbyte(base + 0x01) * 256 + memory.readbyte(base + 0x02)
                local max_hp = memory.readbyte(base + 0x22) * 256 + memory.readbyte(base + 0x23)
                local moves = {
                    memory.readbyte(base + 0x08), memory.readbyte(base + 0x09),
                    memory.readbyte(base + 0x0A), memory.readbyte(base + 0x0B)
                }
                local pp = {
                    bit.band(memory.readbyte(base + 0x1D), 0x3F),
                    bit.band(memory.readbyte(base + 0x1E), 0x3F),
                    bit.band(memory.readbyte(base + 0x1F), 0x3F),
                    bit.band(memory.readbyte(base + 0x20), 0x3F)
                }
                table.insert(party, {
                    name_bytes = name_bytes,
                    species = memory.readbyte(base),
                    level = level,
                    hp = hp,
                    max_hp = max_hp,
                    moves = moves,
                    pp = pp,
                })
            end
            state.party = party
        end
    end)
    if not ok_party then
        state.party = {}
    end

    local ok_bag, _ = pcall(function()
        local count = memory.readbyte(0xD31D)
        local bag = {}
        if count > 0 and count <= 20 then
            for i = 0, count - 1 do
                local id = memory.readbyte(0xD31E + i * 2)
                if id == 0xFF then break end
                table.insert(bag, {
                    id = id,
                    qty = memory.readbyte(0xD31F + i * 2),
                })
            end
        end
        state.bag = bag
    end)
    if not ok_bag then
        state.bag = {}
    end

    state.auto_walk = auto_walk_active
    if auto_walk_active and auto_walk_path then
        state.walk_steps_left = #auto_walk_path - auto_walk_index
    else
        state.walk_steps_left = 0
    end

    local menu_data = nil
    if screen ~= nil then
        local ok_menu, mdata = pcall(extract_menu_items)
        if ok_menu and mdata then
            menu_data = mdata
        end
    end

    state.menu_items = menu_data and menu_data.items or {}
    state.menu_selected = menu_data and menu_data.selected or -1

    local battle_menu_data = nil
    if screen ~= nil then
        local ok_bm, bmdata = pcall(extract_battle_menu)
        if ok_bm and bmdata then
            battle_menu_data = bmdata
        end
    end
    state.battle_menu = battle_menu_data and battle_menu_data.items or {}
    state.battle_menu_selected = battle_menu_data and battle_menu_data.selected or -1

    local screen_lines = {}
    if screen ~= nil and screen.lines then
        for i = 1, #screen.lines do
            local ln = screen.lines[i]
            if type(ln) == "string" then
                table.insert(screen_lines, ln)
            end
        end
    end
    state.screen_lines = screen_lines

    local is_keyboard = false
    if screen ~= nil then
        local ok_kb, kb = pcall(function() return screen:keyboard_showing() end)
        is_keyboard = ok_kb and kb or false
    end
    state.keyboard = is_keyboard

    if is_keyboard then
        local ok_kbl, kbl = pcall(function()
            if RAM_KEYBOARD_POSITION ~= nil then
                local pos = memory.readword(RAM_KEYBOARD_POSITION) + 1
                if RAM_KEYBOARD_END and pos == RAM_KEYBOARD_END then
                    return "END"
                elseif RAM_KEYBOARD_CASE and pos == RAM_KEYBOARD_CASE then
                    return "CASE"
                else
                    return translate(memory.readbyte(pos))
                end
            elseif RAM_KEYBOARD_X ~= nil then
                local x = memory.readbyte(RAM_KEYBOARD_X)
                local y = memory.readbyte(RAM_KEYBOARD_Y)
                local t = KEYBOARD_UPPER
                if screen and screen.lines and screen.lines[17]
                   and KEYBOARD_UPPER_STRING
                   and screen.lines[17]:match(KEYBOARD_UPPER_STRING) then
                    t = KEYBOARD_LOWER
                end
                if t and t[y+1] and t[y+1][x+1] then
                    return t[y+1][x+1]
                end
                return "?"
            else
                return "?"
            end
        end)
        state.keyboard_letter = ok_kbl and kbl or "?"
    else
        state.keyboard_letter = ""
    end

    if auto_walk_active then
        state.mode = "walking"
    elseif is_in_battle then
        state.mode = "battle"
        local battle = {}
        local ok_bt, bt = pcall(function() return memory.readbyte(0xD057) end)
        battle.battle_type = ok_bt and bt or 0

        local ok_bf, bf = pcall(function() return memory.readbyte(0xD05A) end)
        battle.battle_type_flag = ok_bf and bf or 0

        local ok_p, _ = pcall(function()
            local name_bytes = {}
            for i = 0, 10 do
                local b = memory.readbyte(0xD009 + i)
                if b == 0x50 then break end
                table.insert(name_bytes, b)
            end
            battle.player_name = name_bytes
            battle.player_level = memory.readbyte(0xD022)
            battle.player_hp = memory.readbyte(0xD015) * 256 + memory.readbyte(0xD016)
            battle.player_max_hp = memory.readbyte(0xD023) * 256 + memory.readbyte(0xD024)
            battle.player_moves = {
                memory.readbyte(0xD01C), memory.readbyte(0xD01D),
                memory.readbyte(0xD01E), memory.readbyte(0xD01F)
            }
            battle.player_pp = {
                bit.band(memory.readbyte(0xD02D), 0x3F),
                bit.band(memory.readbyte(0xD02E), 0x3F),
                bit.band(memory.readbyte(0xD02F), 0x3F),
                bit.band(memory.readbyte(0xD030), 0x3F)
            }
            battle.player_status = memory.readbyte(0xD018)
        end)

        local ok_e, _ = pcall(function()
            local name_bytes = {}
            for i = 0, 10 do
                local b = memory.readbyte(0xCFDA + i)
                if b == 0x50 then break end
                table.insert(name_bytes, b)
            end
            battle.enemy_name = name_bytes
            battle.enemy_level = memory.readbyte(0xCFF3)
            battle.enemy_hp = memory.readbyte(0xCFE6) * 256 + memory.readbyte(0xCFE7)
            battle.enemy_max_hp = memory.readbyte(0xCFF4) * 256 + memory.readbyte(0xCFF5)
            battle.enemy_status = memory.readbyte(0xCFE9)
        end)

        state.battle = battle
    elseif is_keyboard then
        state.mode = "keyboard"
    elseif menu_data and #menu_data.items > 0 then
        state.mode = "menu"
    elseif #text_lines > 0 then
        state.mode = "dialogue"
    else
        state.mode = "overworld"
    end

    local MAX_OBJ_DISTANCE = 15
    local MAX_SIGNPOST_DISTANCE = 4  -- signposts are read up-close; tighter filter kills garbage
    local MAX_OBJECTS = 10
    if state.mode == "overworld" or state.mode == "dialogue" then
        local ok_info, info = pcall(get_map_info)
        if ok_info and info and info.objects then
            local signpost_count = 0
            for _, obj in ipairs(info.objects) do
                if obj.type == "signpost" then signpost_count = signpost_count + 1 end
            end
            local skip_signposts = (signpost_count > 10)

            local candidates = {}
            for i, obj in ipairs(info.objects) do
                if skip_signposts and obj.type == "signpost" then
                elseif obj.type == "connection" then
                    local entry = {
                        idx = i,
                        type = "connection",
                        x = obj.x or state.player_x or 0,
                        y = obj.y or state.player_y or 0,
                        _dist = 0,
                    }
                    local ok_n, obj_name = pcall(get_name, info.mapid, obj)
                    entry.name = ok_n and obj_name or (obj.name or "???")
                    entry.dist = ""
                    if obj.direction then
                        entry.connection_dir = obj.direction
                    end
                    table.insert(candidates, entry)
                elseif obj.x and obj.y and state.player_x >= 0 and state.player_y >= 0 then
                    local dist = math.abs(obj.x - state.player_x) + math.abs(obj.y - state.player_y)
                    local max_dist = MAX_OBJ_DISTANCE
                    if obj.type == "signpost" then
                        max_dist = MAX_SIGNPOST_DISTANCE
                    elseif obj.type == "connection" then
                        max_dist = 9999
                    end
                    if dist <= max_dist then
                        local entry = {
                            idx = i,
                            type = obj.type or "unknown",
                            x = obj.x,
                            y = obj.y,
                            _dist = dist,
                        }
                        local ok_n, obj_name = pcall(get_name, info.mapid, obj)
                        entry.name = ok_n and obj_name or (obj.name or "???")
                        local ok_d, dir_str = pcall(direction, state.player_x, state.player_y, obj.x, obj.y)
                        entry.dist = ok_d and dir_str or ""
                        if obj.direction then
                            entry.connection_dir = obj.direction
                        end
                        table.insert(candidates, entry)
                    end
                end
            end
            table.sort(candidates, function(a, b) return a._dist < b._dist end)
            local objects = {}
            local obj_count = 0
            for _, e in ipairs(candidates) do
                if e.type == "connection" then
                    e._dist = nil
                    table.insert(objects, e)
                elseif obj_count < MAX_OBJECTS then
                    e._dist = nil
                    table.insert(objects, e)
                    obj_count = obj_count + 1
                end
            end
            state.objects = objects
        else
            state.objects = {}
        end
    else
        state.objects = {}
    end

    state.tolk_output = {}
    for _, line in ipairs(tolk_lines) do
        table.insert(state.tolk_output, line)
    end
    if #tolk_lines > 100 then
        local trimmed = {}
        for i = #tolk_lines - 49, #tolk_lines do
            table.insert(trimmed, tolk_lines[i])
        end
        tolk_lines = trimmed
    end

    return state
end

local SCREENSHOT_REQUEST = BRIDGE_DIR .. "screenshot.req"
local function dump_screenshot()
    local ok, gd = pcall(gui.gdscreenshot)
    if not ok or type(gd) ~= "string" then return end
    local tmp = BRIDGE_DIR .. "screen.tmp"
    local f = io.open(tmp, "wb")
    if f then
        f:write(gd)
        f:close()
        os.remove(BRIDGE_DIR .. "screen.gd")
        os.rename(tmp, BRIDGE_DIR .. "screen.gd")
    end
end

local function log_bridge_error(where, err)
    auto_walk_active = false
    auto_walk_path = nil
    local f = io.open(BRIDGE_DIR .. "bridge_errors.txt", "a")
    if f then
        f:write(os.date("%Y-%m-%dT%H:%M:%S") .. " " .. where .. ": " .. tostring(err) .. "\n")
        f:close()
    end
end

local function process_commands()
    local f = io.open(CMD_FILE, "r")
    if not f then return end
    local content = f:read("*a")
    f:close()
    os.remove(CMD_FILE)

    if not content or content == "" then return end

    tolk_lines = {}

    for line in content:gmatch("[^\r\n]+") do
        line = line:match("^%s*(.-)%s*$")  -- trim
        if line == "" then
        elseif line:sub(1, 6) == "PRESS:" then
            local button = line:sub(7)
            table.insert(button_queue, {button = button, frames = PRESS_FRAMES})

        elseif line:sub(1, 9) == "PATHFIND:" then
            local idx = tonumber(line:sub(10))
            if idx then
                local ok, info = pcall(get_map_info)
                if ok and info and info.objects and info.objects[idx] then
                    local obj = info.objects[idx]
                    local path = nil
                    if obj.type == "connection" then
                        path = find_path_to_xy(obj.x, obj.y)
                    else
                        path = find_path_to_xy(obj.x, obj.y)
                    end
                    if path then
                        auto_walk_path = path
                        auto_walk_index = 1
                        auto_walk_active = true
                        auto_walk_map_id = get_map_id()
                        auto_walk_stuck = 0
                        auto_walk_prev_x = nil
                        auto_walk_prev_y = nil
                    end
                end
            end

        elseif line:sub(1, 11) == "PATHFINDXY:" then
            local coords = line:sub(12)
            local x, y = coords:match("(%d+),(%d+)")
            x, y = tonumber(x), tonumber(y)
            if x and y then
                local path = find_path_to_xy(x, y)
                if path then
                    auto_walk_path = path
                    auto_walk_index = 1
                    auto_walk_active = true
                    auto_walk_map_id = get_map_id()
                    auto_walk_stuck = 0
                    auto_walk_prev_x = nil
                    auto_walk_prev_y = nil
                end
            end

        elseif line:sub(1, 5) == "TYPE:" then
            local text = line:sub(6)
            for i = 1, #text do
                local ch = text:sub(i, i)
                table.insert(button_queue, {button = ch, frames = PRESS_FRAMES, is_char = true})
            end

        elseif line:sub(1, 5) == "SAVE:" then
            local name = line:sub(6)
            local save_path = SAVE_BASE .. name .. ".sgm"
            savestate.savefile(save_path)

        elseif line:sub(1, 5) == "LOAD:" then
            local name = line:sub(6)
            local save_path = SAVE_BASE .. name .. ".sgm"
            savestate.loadfile(save_path)

        elseif line == "INTERACT" then
            table.insert(button_queue, {button = "A", frames = PRESS_FRAMES})

        elseif line == "CANCEL" then
            table.insert(button_queue, {button = "B", frames = PRESS_FRAMES})

        elseif line == "START" then
            table.insert(button_queue, {button = "start", frames = PRESS_FRAMES})

        elseif line == "SELECT" then
            table.insert(button_queue, {button = "select", frames = PRESS_FRAMES})

        elseif line == "STOP_WALK" then
            auto_walk_active = false
            auto_walk_path = nil

        elseif line:sub(1, 4) == "NAV:" then
            local dir = line:sub(5)
            if dir == "UP" then
                table.insert(button_queue, {button = "up", frames = PRESS_FRAMES})
            elseif dir == "DOWN" then
                table.insert(button_queue, {button = "down", frames = PRESS_FRAMES})
            elseif dir == "LEFT" then
                table.insert(button_queue, {button = "left", frames = PRESS_FRAMES})
            elseif dir == "RIGHT" then
                table.insert(button_queue, {button = "right", frames = PRESS_FRAMES})
            elseif dir == "A" then
                table.insert(button_queue, {button = "A", frames = PRESS_FRAMES})
            elseif dir == "B" then
                table.insert(button_queue, {button = "B", frames = PRESS_FRAMES})
            end

        elseif line:sub(1, 7) == "BATTLE:" then
            local pos = tonumber(line:sub(8))
            if pos then
                table.insert(button_queue, {button = "up", frames = PRESS_FRAMES})
                table.insert(button_queue, {button = "left", frames = PRESS_FRAMES})
                if pos == 1 then      -- top-right
                    table.insert(button_queue, {button = "right", frames = PRESS_FRAMES})
                elseif pos == 2 then  -- bottom-left
                    table.insert(button_queue, {button = "down", frames = PRESS_FRAMES})
                elseif pos == 3 then  -- bottom-right
                    table.insert(button_queue, {button = "down", frames = PRESS_FRAMES})
                    table.insert(button_queue, {button = "right", frames = PRESS_FRAMES})
                end
                table.insert(button_queue, {button = "A", frames = PRESS_FRAMES})
            end

        elseif line == "CLEAR_INPUT_LOG" then
            local f = io.open(INPUT_LOG_FILE, "w")
            if f then f:close() end
        end
    end
end

local function execute_auto_walk()
    if not auto_walk_active or not auto_walk_path then return end

    local ok_mid, cur_map = pcall(get_map_id)
    if ok_mid and auto_walk_map_id and cur_map ~= auto_walk_map_id then
        auto_walk_active = false
        auto_walk_path = nil
        return
    end

    local ok, px, py = pcall(get_player_xy)
    if not ok then return end

    while auto_walk_index <= #auto_walk_path do
        local node = auto_walk_path[auto_walk_index]
        if px == node.x and py == node.y then
            auto_walk_index = auto_walk_index + 1
        else
            break
        end
    end

    if auto_walk_index > #auto_walk_path then
        auto_walk_active = false
        auto_walk_path = nil
        return
    end

    local target = auto_walk_path[auto_walk_index]

    local dir = nil
    if target.y < py then dir = "up"
    elseif target.y > py then dir = "down"
    elseif target.x < px then dir = "left"
    elseif target.x > px then dir = "right"
    end

    if dir then
        joypad.set(1, {[dir] = true})

        if auto_walk_prev_x == px and auto_walk_prev_y == py then
            auto_walk_stuck = auto_walk_stuck + 1
            if auto_walk_stuck >= 60 then
                auto_walk_active = false
                auto_walk_path = nil
                return
            end
        else
            auto_walk_stuck = 0
        end
        auto_walk_prev_x = px
        auto_walk_prev_y = py
    else
        auto_walk_active = false
        auto_walk_path = nil
    end
end

local function process_button_queue()
    if #button_queue == 0 then return end

    local current = button_queue[1]
    if current.frames > 0 then
        if not current.is_char then
            joypad.set(1, {[current.button] = true})
        end
        current.frames = current.frames - 1
    else
        table.remove(button_queue, 1)
    end
end

local function export_state()
    local ok, state = pcall(build_state)
    if not ok then
        state = {ready = true, mode = "error", error_msg = tostring(state)}
    end

    local ok2, json = pcall(json_encode, state)
    if ok2 then
        if json ~= last_state_json then
            write_atomic(STATE_FILE, STATE_TMP, json)
            last_state_json = json
        end
    end
end

local function write_ready()
    write_atomic(STATE_FILE, STATE_TMP, '{"ready":true,"mode":"loading","map_name":"Loading...","objects":[],"text_lines":[],"tolk_output":[],"menu_items":[],"menu_selected":-1,"battle_menu":[],"battle_menu_selected":-1,"screen_lines":[],"auto_walk":false,"walk_steps_left":0}')
end

write_ready()

PRESS_FRAMES = get_hold_frames()

while true do
    emu.frameadvance()

    local ok, err = pcall(main_loop)
    if not ok then
    end

    frame_counter = frame_counter + 1
    input_frame_counter = input_frame_counter + 1

    local ok_commands, command_error = pcall(process_commands)
    if not ok_commands then
        log_bridge_error("command", command_error)
    end
    local screenshot_request = io.open(SCREENSHOT_REQUEST, "r")
    if screenshot_request then
        screenshot_request:close()
        os.remove(SCREENSHOT_REQUEST)
        dump_screenshot()
    end

    if auto_walk_active then
        local ok_walk, walk_error = pcall(execute_auto_walk)
        if not ok_walk then
            log_bridge_error("auto_walk", walk_error)
        end
    else
        process_button_queue()
    end

    if frame_counter % EXPORT_INTERVAL == 0 then
        export_state()
    end
end
