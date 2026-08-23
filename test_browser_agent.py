import time
from ultron.brain import Brain

def run_tests():
    b = Brain()
    print("Initializing Ultron Brain...")
    time.sleep(2)  # Give it a moment to initialize
    
    tasks = [
        "Go to Wikipedia and find the capital of France, then tell me its population.",
        "Navigate to Hacker News (news.ycombinator.com) and tell me the title of the top story right now.",
        "Go to GitHub and search for the repository 'torvalds/linux' and tell me its description.",
        "Find out the current weather in Tokyo by searching Google.",
        "Go to python.org, navigate to the 'Downloads' section, and tell me the latest stable release version of Python."
    ]
    
    results = {}
    
    for i, task in enumerate(tasks, 1):
        print(f"\n{'='*50}")
        print(f"Test {i}/{len(tasks)}: {task}")
        print(f"{'='*50}\n")
        
        try:
            result = b._invoke_tool('start_browser_agent', {'goal': task})
            print(f"\n[FINAL RESULT for Test {i}]:\n{result}")
            results[task] = result
        except Exception as e:
            print(f"\n[ERROR for Test {i}]: {e}")
            results[task] = str(e)
            
        print("\nWaiting 5 seconds before next test...")
        time.sleep(5)
        
    print("\n\n" + "="*50)
    print("ALL TESTS COMPLETED")
    print("="*50)
    for task, res in results.items():
        print(f"\nGoal: {task}")
        print(f"Result: {res[:200]}..." if len(res) > 200 else f"Result: {res}")

if __name__ == "__main__":
    run_tests()
