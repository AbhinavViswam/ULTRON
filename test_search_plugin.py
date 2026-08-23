import time
from ultron.plugins.search_plugin import search_and_open

def run_tests():
    print("Testing search_and_open with 5 different queries...\n")
    
    tasks = [
        "brain.py",
        "calc.exe",
        "Downloads",
        "test_browser_agent.py",
        "NonExistentFile12345.xyz"
    ]
    
    for i, task in enumerate(tasks, 1):
        print(f"{'='*50}")
        print(f"Test {i}/{len(tasks)}: Search for '{task}'")
        print(f"{'='*50}")
        
        try:
            result = search_and_open(task)
            print(f"\n[RESULT for Test {i}]:\n{result}")
        except Exception as e:
            print(f"\n[ERROR for Test {i}]: {e}")
            
        print("\nWaiting 2 seconds before next test...\n")
        time.sleep(2)
        
    print("="*50)
    print("All tests completed.")

if __name__ == "__main__":
    run_tests()
