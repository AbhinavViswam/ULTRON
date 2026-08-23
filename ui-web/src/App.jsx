import { useState, useEffect, useRef } from 'react';
import ChatSidebar from './ChatSidebar';
import SettingsSidebar from './SettingsSidebar';
import OrbCore from './OrbCore';
import ToolContainer from './ToolContainer';

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [socket, setSocket] = useState(null);
  const [confirmation, setConfirmation] = useState(null);
  const [isConnected, setIsConnected] = useState(false);
  
  // New State variables
  const [isChatOpen, setIsChatOpen] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [botState, setBotState] = useState('idle');
  const [activeTool, setActiveTool] = useState(null);
  const [micActive, setMicActive] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname || 'localhost';
    const port = '8000';
    const wsUrl = `${protocol}//${host}:${port}/ws/chat`;
    
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'bot_message' || msg.type === 'user_message') {
          setMessages(prev => [...prev, {
            id: Date.now() + Math.random(),
            role: msg.type === 'bot_message' ? 'ultron' : 'user',
            text: msg.data.text,
            source: msg.data.source || msg.data.origin,
            queued: msg.data.queued
          }]);
          if (msg.type === 'bot_message') setBotState('idle');
        } else if (msg.type === 'confirmation_request') {
          setConfirmation(msg.data.question);
        } else if (msg.type === 'tool_event') {
          if (msg.data.phase === 'start') {
            setActiveTool(msg.data.name);
          } else {
            setActiveTool(null);
          }
        } else if (msg.type === 'state_change') {
          setBotState(msg.data.state);
        } else if (msg.type === 'audio_level') {
          setAudioLevel(msg.data.level);
        } else if (msg.type === 'init_state') {
          setMicActive(msg.data.mic_active);
          setBotState(msg.data.bot_state);
        }
      } catch (err) {
        console.error('Failed to parse WebSocket message', err);
      }
    };

    setSocket(ws);
    return () => ws.close();
  }, []);

  const handleSend = (e) => {
    e.preventDefault();
    if (!input.trim() || !socket || !isConnected) return;
    
    setMessages(prev => [...prev, {
      id: Date.now(),
      role: 'user',
      text: input,
      source: 'web'
    }]);

    setBotState('thinking');
    socket.send(JSON.stringify({ type: 'chat', text: input }));
    setInput('');
  };

  const handleConfirm = async (approved) => {
    try {
      await fetch(`http://${window.location.hostname}:8000/api/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved })
      });
      setConfirmation(null);
      setMessages(prev => [...prev, {
        id: Date.now(),
        role: 'system',
        text: approved ? '[Action Approved]' : '[Action Denied]'
      }]);
    } catch (err) {
      console.error('Confirmation failed', err);
    }
  };

  const toggleMic = () => {
    const nextState = !micActive;
    setMicActive(nextState);
    if (socket && isConnected) {
      socket.send(JSON.stringify({ type: 'toggle_mic', active: nextState }));
    }
  };

  return (
    <div className="relative min-h-screen bg-slate-950 overflow-hidden font-sans text-slate-200">
      
      {/* Background ambient lighting */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-slate-950 -z-10" />

      {/* Main UI Container */}
      <div className="flex h-screen w-full relative">
        
        {/* Chat Sidebar */}
        <ChatSidebar 
          isOpen={isChatOpen} 
          onClose={() => setIsChatOpen(false)} 
          messages={messages}
          input={input}
          setInput={setInput}
          handleSend={handleSend}
          isConnected={isConnected}
        />

        {/* Center Stage (Orb) */}
        <main className={`flex-1 flex items-center justify-center relative transition-all duration-300 ${isChatOpen ? 'ml-[400px]' : 'ml-0'}`}>
          <OrbCore state={botState} audioLevel={audioLevel} />
          
          <ToolContainer activeTool={activeTool} isConnected={isConnected} />

          {/* Top Navigation / Controls */}
          <div className="absolute top-6 left-6 right-6 flex justify-between items-center z-30">
            <button 
              onClick={() => setIsChatOpen(!isChatOpen)}
              className="p-2 bg-slate-800/50 hover:bg-slate-700/50 rounded-lg backdrop-blur-md border border-slate-700/50 text-slate-300 transition-colors"
            >
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h7" /></svg>
            </button>
            
            <div className="flex items-center gap-4">
              <button 
                onClick={toggleMic}
                className={`flex items-center gap-2 px-4 py-2 rounded-full border backdrop-blur-md transition-all ${
                  micActive 
                    ? 'bg-red-500/20 border-red-500/50 text-red-400 shadow-[0_0_15px_rgba(239,68,68,0.3)] animate-pulse' 
                    : 'bg-slate-800/50 border-slate-700/50 text-slate-300 hover:bg-slate-700/50'
                }`}
              >
                <svg className="w-5 h-5" fill={micActive ? "currentColor" : "none"} stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" /></svg>
                {micActive ? 'Live' : 'Mic Off'}
              </button>

              <button 
                onClick={() => setIsSettingsOpen(true)}
                className="p-2 bg-slate-800/50 hover:bg-slate-700/50 rounded-lg backdrop-blur-md border border-slate-700/50 text-slate-300 transition-colors"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
              </button>
            </div>
          </div>
        </main>
      </div>

      <SettingsSidebar isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />

      {/* Confirmation Modal */}
      {confirmation && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm p-4">
          <div className="glass-panel p-6 rounded-2xl max-w-md w-full border border-orange-500/30 fade-in-up shadow-[0_0_40px_rgba(249,115,22,0.1)]">
            <h3 className="text-lg font-bold text-orange-400 mb-2 flex items-center gap-2">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              Permission Required
            </h3>
            <p className="text-slate-200 mb-6">{confirmation}</p>
            <div className="flex justify-end gap-3">
              <button 
                onClick={() => handleConfirm(false)}
                className="px-4 py-2 rounded-lg font-medium text-slate-300 hover:text-white bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700"
              >
                Deny
              </button>
              <button 
                onClick={() => handleConfirm(true)}
                className="px-4 py-2 rounded-lg font-medium text-white bg-primary hover:bg-primary/90 shadow-lg shadow-primary/30 transition-all active:scale-95"
              >
                Allow Action
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
