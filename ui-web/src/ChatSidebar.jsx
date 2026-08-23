import React from 'react';
import ChatBox from './ChatBox';

const ChatSidebar = ({ isOpen, onClose, messages, input, setInput, handleSend, isConnected }) => {
  return (
    <div className={`fixed left-0 top-0 bottom-0 w-[400px] glass-panel border-r border-slate-700/50 transform transition-transform duration-300 z-40 flex flex-col ${
      isOpen ? 'translate-x-0' : '-translate-x-full'
    }`}>
      {/* Header */}
      <header className="px-6 py-4 border-b border-slate-700/50 flex items-center justify-between shrink-0 bg-slate-900/40">
        <div className="flex items-center gap-3">
          <div className={`w-3 h-3 rounded-full ${isConnected ? 'bg-orange-400 shadow-[0_0_8px_rgba(251,146,60,0.8)] pulse-glow' : 'bg-red-500'}`} />
          <h1 className="text-xl font-bold tracking-tight text-white">Ultron <span className="text-primary font-light">Chat</span></h1>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-white md:hidden">
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </header>

      {/* Chat Area */}
      <div className="flex-1 overflow-y-auto p-4 scroll-smooth">
        <ChatBox messages={messages} />
      </div>

      {/* Input Area */}
      <footer className="p-4 shrink-0 bg-slate-900/40 border-t border-slate-700/50">
        <form onSubmit={handleSend} className="relative flex items-center">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="Ask Ultron something..."
            disabled={!isConnected}
            className="w-full bg-slate-800/50 text-white placeholder-slate-400 border border-slate-600 rounded-full py-3 pl-5 pr-12 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all disabled:opacity-50 text-sm"
          />
          <button 
            type="submit"
            disabled={!input.trim() || !isConnected}
            className="absolute right-2 p-1.5 rounded-full bg-primary text-white hover:bg-primary/90 disabled:opacity-50 disabled:bg-slate-700 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </button>
        </form>
      </footer>
    </div>
  );
};

export default ChatSidebar;
