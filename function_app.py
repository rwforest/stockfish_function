import logging
import azure.functions as func
import chess
import chess.engine
import os
import json
import time

# --- Configuration ---
# Adjust this path based on where you place the Stockfish executable relative to this script
# For Linux, if 'stockfish' is in a 'bin' subdirectory of the function folder:
STOCKFISH_PATH = os.path.join(os.path.dirname(__file__), 'bin', 'stockfish')
# If you are on Windows for local testing (ensure your Azure Function OS matches for deployment)
# STOCKFISH_PATH = os.path.join(os.path.dirname(__file__), 'bin', 'stockfish.exe')


async def get_stockfish_analysis(fen_string: str, depth_limit: int = 12, time_limit_sec: float = 5.0):
    """
    Analyzes a FEN position using Stockfish.
    """
    if not os.path.exists(STOCKFISH_PATH):
        raise FileNotFoundError(f"Stockfish executable not found at {STOCKFISH_PATH}")
    if not os.access(STOCKFISH_PATH, os.X_OK):
        # Attempt to set execute permissions if on a writable filesystem (might not work in all Azure environments post-deployment)
        # It's best to ensure execute permissions are set before deployment.
        try:
            os.chmod(STOCKFISH_PATH, 0o755) # rwxr-xr-x
            logging.warning(f"Attempted to set execute permission for {STOCKFISH_PATH}")
            if not os.access(STOCKFISH_PATH, os.X_OK):
                 raise PermissionError(f"Stockfish executable at {STOCKFISH_PATH} is not executable after chmod.")
        except Exception as e:
            raise PermissionError(f"Stockfish executable at {STOCKFISH_PATH} is not executable. Error: {e}")


    transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
    board = chess.Board(fen_string)

    analysis_result = {}
    start_time = time.time()

    try:
        # Configure Stockfish (optional, but good practice)
        # await engine.configure({"Threads": 1}) # Adjust based on your Azure Function plan
        # await engine.configure({"Hash": 32})   # Adjust memory

        info = await engine.analyse(board, chess.engine.Limit(depth=depth_limit, time=time_limit_sec))
        end_time = time.time()

        best_move = info.get("pv", [None])[0] # Principal variation's first move
        score = info.get("score")

        analysis_result = {
            "fen": fen_string,
            "depth": info.get("depth"),
            "seldepth": info.get("seldepth"), # Selective depth
            "nodes": info.get("nodes"),
            "nps": info.get("nps"), # Nodes per second
            "time": int((end_time - start_time) * 1000), # milliseconds
            "mate": None,
            "eval": None,
            "centipawns": None,
            "text": "",
            "move": None,
            "san": None,
            "lan": None,
            "turn": "w" if board.turn == chess.WHITE else "b",
            "color": "w" if board.turn == chess.WHITE else "b", # Assuming 'color' means current turn's color
            "piece": None,
            "flags": None,
            "isCapture": None,
            "isCastling": None,
            "isPromotion": None,
            "from": None,
            "to": None,
            "fromNumeric": None,
            "toNumeric": None,
            "continuationArr": [move.uci() for move in info.get("pv", [])],
            "winChance": None # Calculating this accurately requires a specific formula
        }

        if score:
            if score.is_mate():
                analysis_result["mate"] = score.mate()
                analysis_result["eval"] = float('inf') if score.mate() > 0 else float('-inf')
                analysis_result["centipawns"] = "mate " + str(score.mate())
                analysis_result["text"] = f"Mate in {abs(score.mate())}. Depth {info.get('depth')}."
            else:
                cp = score.relative.score(mate_score=10000) # Centipawns relative to current player
                analysis_result["eval"] = cp / 100.0
                analysis_result["centipawns"] = str(cp)
                winning_status = "winning" if cp > 50 else "losing" if cp < -50 else "equal"
                player_turn = "White" if board.turn == chess.WHITE else "Black"
                if cp < 0 and board.turn == chess.WHITE : winning_status = "losing"
                if cp > 0 and board.turn == chess.BLACK : winning_status = "losing"


                analysis_result["text"] = f"Eval: {cp/100.0:.2f}. {player_turn} is {winning_status}. Depth {info.get('depth')}."


        if best_move:
            analysis_result["move"] = best_move.uci()
            analysis_result["lan"] = best_move.uci()
            analysis_result["san"] = board.san(best_move)
            analysis_result["from"] = chess.square_name(best_move.from_square)
            analysis_result["to"] = chess.square_name(best_move.to_square)
            analysis_result["fromNumeric"] = str(best_move.from_square) # numeric representation (0-63)
            analysis_result["toNumeric"] = str(best_move.to_square)   # numeric representation (0-63)

            moved_piece = board.piece_at(best_move.from_square)
            if moved_piece:
                analysis_result["piece"] = moved_piece.symbol().lower()

            analysis_result["isCapture"] = board.is_capture(best_move)
            analysis_result["isCastling"] = board.is_castling(best_move)
            analysis_result["isPromotion"] = best_move.promotion is not None
            analysis_result["flags"] = get_move_flags(board, best_move)

            # Update text with best move
            if analysis_result["san"]:
                 analysis_result["text"] = f"Best move {analysis_result['san']}: [{analysis_result.get('eval', 'N/A')}]. {analysis_result['text'].split('. ', 1)[-1]}"


        # Basic win chance calculation (example, can be more sophisticated)
        # Using a simple sigmoid-like function for centipawns
        if analysis_result["eval"] is not None and not score.is_mate():
            # K is a scaling factor, you might need to adjust it
            # This formula is just an example, not a standard.
            k_factor = 0.004
            win_chance_current_player = 1 / (1 + 10**(-k_factor * analysis_result["centipawns"]))
            analysis_result["winChance"] = win_chance_current_player if board.turn == chess.WHITE else 1 - win_chance_current_player


    except chess.engine.EngineTerminatedError as e:
        logging.error(f"Stockfish engine terminated: {e}")
        raise
    except Exception as e:
        logging.error(f"Error during Stockfish analysis: {e}")
        raise
    finally:
        await engine.quit()

    return analysis_result

def get_move_flags(board, move):
    """Generates move flags similar to chess.js (n,b,e,c,p,k,q)."""
    flags = ""
    if move.promotion:
        flags += "p" # Promotion
    elif board.is_kingside_castling(move):
        flags += "k" # Kingside castling
    elif board.is_queenside_castling(move):
        flags += "q" # Queenside castling

    if board.is_capture(move):
        if board.is_en_passant(move):
            flags += "e" # En passant
        else:
            flags += "c" # Capture
    
    if not flags and not board.is_capture(move) and not move.promotion: # Normal move
        flags = "n"
    # 'b' for pawn push by 2 squares - python-chess doesn't directly flag this in move object,
    # but you can infer it if piece is pawn and it moves two squares.
    piece = board.piece_at(move.from_square)
    if piece and piece.piece_type == chess.PAWN:
        if abs(move.to_square // 8 - move.from_square // 8) == 2:
            flags = "b" + flags.replace("n","") # Big pawn push

    return flags


async def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a stockfish_eval request.')

    fen = None
    depth = 12 # Default depth
    req_body = {}

    try:
        req_body = req.get_json()
    except ValueError:
        pass # Handle cases where body isn't JSON or is empty
    else:
        fen = req_body.get('fen')
        depth = int(req_body.get('depth', 12)) # Allow overriding depth

    if not fen:
        fen = req.params.get('fen')
        if req.params.get('depth'):
            depth = int(req.params.get('depth'))


    if fen:
        try:
            # Basic FEN validation
            try:
                board_test = chess.Board(fen)
            except ValueError:
                return func.HttpResponse(
                    json.dumps({"error": "Invalid FEN string provided."}),
                    status_code=400,
                    mimetype="application/json"
                )

            # Call the analysis function
            analysis_results = await get_stockfish_analysis(fen, depth_limit=depth)
            analysis_results["taskId"] = req.headers.get("X-Request-ID", "defaultTaskId") # Example for taskId

            return func.HttpResponse(
                json.dumps(analysis_results),
                status_code=200,
                mimetype="application/json"
            )
        except FileNotFoundError as e:
            logging.error(f"Stockfish setup error: {e}")
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                mimetype="application/json"
            )
        except PermissionError as e:
            logging.error(f"Stockfish permission error: {e}")
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                mimetype="application/json"
            )
        except Exception as e:
            logging.exception(f"An error occurred during analysis for FEN: {fen}")
            return func.HttpResponse(
                json.dumps({"error": f"An internal error occurred: {str(e)}"}),
                status_code=500,
                mimetype="application/json"
            )
    else:
        return func.HttpResponse(
             "Please pass a FEN string in the request body (e.g., {'fen': 'your_fen_string'}) or as a query parameter 'fen'.",
             status_code=400
        )