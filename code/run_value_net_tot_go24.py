# pip install pandas torch tiktoken dotenv openai scikit-learn

import util_gameof24
import torch
import time
import random
from train_value_net_go24 import ValueNetwork
from util_gameof24 import extract_features, pad

# Instructions and Prompts
INSTRUCT = "You are a game of 24 grandmaster. We are going to take this problem step by step. At each step, you are going to pick only 2 numbers to operate on. Put only the mathematical expression you choose on the last line and nothing else. Don't put any of the math in latex or markdown formats."
PROMPTS = {
    "step1": "Numbers: ",
    "step2": "Remaining numbers: ",
    "step3": "Remaining numbers: <INSERT NUMBERS>. This is the last step. If you cannot obtain 24, put 'No'.",
    "prevent_repetition": ". Don't pick these operations that you've already tried: "
}

debug = True

# Load game of 24 data
# Note that the hardest problems are stored at the beginning.
df = util_gameof24.papers_data()

# Load weights from pretrained network
value_model = ValueNetwork()
value_model.load_state_dict(torch.load(
    'gameof24/trained_value_net.pth', weights_only=True))
value_model.eval()


def evaluate_states(states):
    """
    Since the ValueNetwork was trained on states that were sorted and padded,
    we should sort and pad correspondingly. Batch evaluates multiple states for
    all efficiency. States is a list of list of numbers.
    """
    processed = [extract_features(s) for s in states]
    x = torch.tensor(processed, dtype=torch.float32)
    with torch.no_grad():
        return value_model(x).squeeze(1).tolist()


def is_valid_equation(given_numbers: list[int], before_equals: str, after_equals: str) -> bool:
    """
    Checks whether the given expression is valid using the following criteria:
    1. Evaluates whether before_equals evaluates to after_equals.
    2. Checks that only two numbers were chosen.
    3. Checks that the a given number was used at most once.
    """
    try:
        if eval(before_equals) != eval(after_equals):
            if debug:
                print(
                    "Invalid Equation: before_equals is not equivalent to after_equals")
            return False
    except SyntaxError:
        print("Cannot eval.")
        return False

    used_numbers = util_gameof24.extract_numbers(before_equals)
    if len(used_numbers) != 2:
        # if debug:
        # print("Invalid Equation: More than two numbers used.")
        return False

    temp = given_numbers.copy()
    for num in used_numbers:
        if num not in temp:
            # if debug:
            #     print(f"Invalid Equation: {num} incorrectly used (duplicate usage/unavailable)")
            return False
        temp.remove(num)
    return True


def find_remaining_nums(original: list, before: str, after: str) -> str:
    """
    Input: original: list of numbers
    ---
    format of original is : '[1,2,3,4]'
    Example: find_remaining_nums([1,2,3,4], "(1 + 2)", "3") will return 3, 4
    """
    used_numbers = util_gameof24.extract_numbers(before)
    output = original.copy()
    for s in used_numbers:
        # can assume s is in output because is_valid_equation is run before this
        output.remove(int(s))
    output.append(eval(after))
    return output


def complete_one_problem(quad: list[int], b: int, k: int = 3):
    """
    Input:
        quad: list of four numbers we are trying to solve.
        b: number of remaining candidates after each step.
        k: determines breadth of candidates we create before pruning.
    ---
    Solves one game of 24 problem with Tree of Thoughts using the following architecture:
        Step 1: Query Chat to combine two of the four initial numbers b * k times.
                Find the k states with the highest scores according to the evaluator.
                Proceed to Step 2 using those k states.
        Step 2: With three numbers remaining, query Chat to combine two numbers b * k times.
                Find the k states with the highest scores according to the evaluator.
                Proceed to Step 3 using those k states.
        Step 3: Check if Chat has reached 24 (and claims to reach 24) in any of the states.
    Total: 2 * b * k queries per problem.
    Note: This function does not append responses from Step 1 to the Step 2 prompt (chaining).
    ---
    Returns: whether Chat correctly solved the game of 24.
    """
    ### Step 1 ###
    step1_raw = []
    step1_responses = []
    total_input_tokens = 0
    total_output_tokens = 0
    if debug:
        print("#######################")
        print("Original Problem:", quad)

    # Step 1: Query Chat to combine two of the four initial numbers b * k times
    for i in range(b * k):
        try:
            before, after, input_tokens, output_tokens = util_gameof24.ask_and_parse(PROMPTS["step1"] + str(quad) +
                                                                                     PROMPTS["prevent_repetition"] + str(step1_responses), INSTRUCT, True, 1.2)
        except SyntaxError:
            return False, total_input_tokens, total_output_tokens
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        if before and after and is_valid_equation(quad, before, after):
            new_nums = find_remaining_nums(quad, before, after)
            new_trace = [f"{before} = {after}"]
            step1_raw.append((new_nums, new_trace))
            step1_responses.append(before)

    if not step1_raw:
        print("Error: Step 1 failed with no valid candidates.")
        return 0, total_input_tokens, total_output_tokens

    # Step 1: Find the k states with the highest scores according to the evaluator.
    scores = evaluate_states([state for state, _ in step1_raw])
    step1_candidates = sorted(zip(scores, step1_raw), reverse=True)[:b]
    top_states = [(score, s, trace) for score, (s, trace) in step1_candidates]
    if debug:
        print(top_states)
        print("\nStep 1 Complete.\n")

    ### Step 2 ###
    step2_raw = []
    step2_responses = []

    # Step 2: Query Chat to combine two of the four initial numbers b * k times
    for score, state, trace in top_states:
        for i in range(k):
            try:
                before, after, input_tokens, output_tokens = util_gameof24.ask_and_parse(PROMPTS["step2"] + str(state) +
                                                                                         PROMPTS["prevent_repetition"] + str(step2_responses), INSTRUCT, True, 1.2)
            except SyntaxError:
                return False, total_input_tokens, total_output_tokens
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            if before and after and is_valid_equation(state, before, after):
                new_nums = find_remaining_nums(state, before, after)
                new_trace = trace + [f"{before} = {after}"]
                step2_raw.append((new_nums, new_trace))
                step2_responses.append(before)

    if not step2_raw:
        print("Error: Step 2 failed with no valid candidates.")
        return 0, total_input_tokens, total_output_tokens

    # Step 2: Find the k states with the highest scores according to the evaluator.
    scores = evaluate_states([state for state, _ in step2_raw])
    step2_candidates = sorted(zip(scores, step2_raw), reverse=True)[:b]
    top_states = [(score, s, trace) for score, (s, trace) in step2_candidates]
    if debug:
        print(top_states)
        print("\nStep 2 Complete.\n")

    ### Step 3 ###
    for score, state, trace in top_states:
        a, b = state
        # 1) Ask Chat up to k times, as before
        for _ in range(k):
            try:
                before, after, input_tokens, output_tokens = util_gameof24.ask_and_parse(
                    PROMPTS["step3"].replace("<INSERT NUMBERS>", str(state)),
                    INSTRUCT, True, 1.0
                )
            except SyntaxError:
                return False, total_input_tokens, total_output_tokens
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            if before and after and is_valid_equation(state, before, after):
                if abs(eval(after) - 24) < 1e-6:
                    print("Chat solved it with:",
                          trace + [f"{before} = {after}"])
                    return 1, total_input_tokens, total_output_tokens

        # 2) **Fallback brute-force** on the last two numbers
        a, b = state
        candidates = {
            f"{a} + {b}": a + b,
            f"{a} - {b}": a - b,
            f"{b} - {a}": b - a,
            f"{a} * {b}": a * b
        }
        if b != 0:
            candidates[f"{a} / {b}"] = a / b
        if a != 0:
            candidates[f"{b} / {a}"] = b / a

        # Pick any that exactly hits 24
        for expr, val in candidates.items():
            if abs(val - 24) < 1e-6:
                print("Fallback solved it with:",
                      trace + [f"{expr} = {int(val)}"])
                return 2, total_input_tokens, total_output_tokens

    print("Chat failed.")
    return 0, total_input_tokens, total_output_tokens


def run_experiment(df, amount, b=5):
    start_time = time.time()
    total = 0
    correct = 0
    fallback = 0

    df_sample = random.sample(df, amount)
    total_input_tokens = 0
    total_output_tokens = 0
    for quad in df_sample:
        res, input_tokens, output_tokens = complete_one_problem(quad, b)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total += 1
        if res == 2 or res == 1:
            correct += 1
        if res == 2:
            fallback += 1
    time_elapsed = time.time() - start_time
    print(f"Used " + str(total_input_tokens) + " input tokens and " +
          str(total_output_tokens) + " output tokens.")
    print(f"This cost " +
          str(util_gameof24.total_cost(total_input_tokens, total_output_tokens)))
    print(f"\nFinished {total} problems in {time_elapsed:.2f} seconds.")
    print("Fellback on " + str(fallback) + " problems.")
    return correct/total


if __name__ == '__main__':
    one, two, three, four, five = util_gameof24.split_data()

    # choose which dataset to run by changing parameter one,two,three, etc.
    print(run_experiment(two, 10))
