import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
import { createClient, Session } from '@supabase/supabase-js'
import ReactMarkdown from 'react-markdown'
import { ArrowDown, ArrowRight, BookOpen, Check, ChevronDown, ChevronLeft, CircleHelp, Clock3, FileSearch, Library, LoaderCircle, LogIn, LogOut, Menu, MessageSquarePlus, MoreHorizontal, Plus, Search, ShieldCheck, Sparkles, X } from 'lucide-react'

type Source = { id:number; title:string; authors?:string; venue?:string; year?:number|string; doi?:string; url?:string; pdf?:string; abstract?:string }
type Report = { question:string; review:string; angles:string[]; sources:Source[]; generated_at:number; critic?:Record<string, unknown> }
type Message = { id:string; role:'user'|'assistant'; text?:string; report?:Report; status?:string; progress?:number; trace?:Array<{step:string;detail:string}> }
type Conversation = { id:string; title:string; updatedAt:number; messages:Message[] }
const examples = [
  'How does remote work affect employee productivity and well-being?',
  'What are effective interventions for reducing food waste in cities?',
  'How does social media use influence adolescent sleep quality?',
  'Which teaching approaches improve early reading outcomes?',
]
const apiBase=import.meta.env.VITE_API_URL || (import.meta.env.DEV?'http://localhost:8000':'')
const authUrl=import.meta.env.VITE_SUPABASE_URL
const authKey=import.meta.env.VITE_SUPABASE_ANON_KEY
const supabase=authUrl&&authKey?createClient(authUrl,authKey):null
type AuthClient=NonNullable<typeof supabase>
const AUTH_REQUIRED=import.meta.env.VITE_REQUIRE_AUTH==='true'
const STORAGE_KEY='litera-conversations-v2'

function restoreConversations(owner:string):Conversation[]{try{return JSON.parse(localStorage.getItem(`${STORAGE_KEY}:${owner}`)||'[]')}catch{return []}}

export default function App(){
  const [session,setSession]=useState<Session|null>(null)
  const [authReady,setAuthReady]=useState(!supabase)
  const [conversations,setConversations]=useState<Conversation[]>([])
  const [activeId,setActiveId]=useState<string|null>(null)
  const [draft,setDraft]=useState('')
  const [busy,setBusy]=useState(false)
  const [authOpen,setAuthOpen]=useState(false)
  const [menuOpen,setMenuOpen]=useState(false)
  const [mobileOpen,setMobileOpen]=useState(false)
  const [error,setError]=useState('')
  const ownerKey=session?.user.id||'guest'
  const inputRef=useRef<HTMLTextAreaElement>(null)
  const active=conversations.find(c=>c.id===activeId)

  useEffect(()=>{if(!supabase){setAuthReady(true);return}supabase.auth.getSession().then(({data})=>{setSession(data.session);setAuthReady(true)});const {data:{subscription}}=supabase.auth.onAuthStateChange((_event,next)=>setSession(next));return()=>subscription.unsubscribe()},[])
  useEffect(()=>{if(!authReady)return;setConversations(restoreConversations(ownerKey));setActiveId(null)},[authReady,ownerKey])
  useEffect(()=>{if(authReady)localStorage.setItem(`${STORAGE_KEY}:${ownerKey}`,JSON.stringify(conversations.slice(0,30)))},[authReady,ownerKey,conversations])
  useEffect(()=>{const target=inputRef.current;if(target){target.style.height='auto';target.style.height=`${Math.min(target.scrollHeight,168)}px`}},[draft])

  const createConversation=(question:string)=>{
    const conversation:Conversation={id:crypto.randomUUID(),title:question,updatedAt:Date.now(),messages:[{id:crypto.randomUUID(),role:'user',text:question},{id:crypto.randomUUID(),role:'assistant',status:'Starting your research',progress:2}]}
    setConversations(prev=>[conversation,...prev]);setActiveId(conversation.id);return {conversationId:conversation.id,assistantId:conversation.messages[1].id}
  }
  const updateAssistant=(conversationId:string,assistantId:string,update:Partial<Message>)=>setConversations(prev=>prev.map(c=>c.id!==conversationId?c:{...c,updatedAt:Date.now(),messages:c.messages.map(m=>m.id===assistantId?{...m,...update}:m)}))
  async function submitResearch(question:string){
    const cleaned=question.trim();if(!cleaned||busy)return
    if(AUTH_REQUIRED&&supabase&&!session){setAuthOpen(true);return}
    if(!apiBase){setError('Research is temporarily unavailable. Please try again later.');return}
    setDraft('');setError('');setBusy(true)
    const {conversationId,assistantId}=createConversation(cleaned)
    try{
      const headers:Record<string,string>={'Content-Type':'application/json'}
      if(session?.access_token)headers.Authorization=`Bearer ${session.access_token}`
      const start=await fetch(`${apiBase}/query`,{method:'POST',headers,body:JSON.stringify({question:cleaned})})
      const startBody=await start.json().catch(()=>({}))
      if(!start.ok)throw new Error(startBody.detail||'We could not start this research. Please try again.')
      const {job_id}=startBody
      for(let attempt=0;attempt<240;attempt++){
        await new Promise(resolve=>setTimeout(resolve,1500))
        const response=await fetch(`${apiBase}/status/${job_id}`,{headers:session?.access_token?{Authorization:`Bearer ${session.access_token}`}:{}})
        const state=await response.json().catch(()=>({}))
        if(!response.ok)throw new Error(state.detail||'We lost connection to this research. Please try again.')
        updateAssistant(conversationId,assistantId,{status:state.stage||'Reviewing academic evidence',progress:state.progress||5,trace:state.trace||[]})
        if(state.status==='failed')throw new Error(state.error||'Research could not be completed. Try a more specific question.')
        if(state.status==='completed'){
          const finalResponse=await fetch(`${apiBase}/report/${job_id}`,{headers:session?.access_token?{Authorization:`Bearer ${session.access_token}`}:{}})
          const report=await finalResponse.json().catch(()=>({}))
          if(!finalResponse.ok)throw new Error(report.detail||'The review was completed but could not be loaded.')
          updateAssistant(conversationId,assistantId,{status:undefined,progress:100,report,trace:state.trace||[]})
          return
        }
      }
      throw new Error('This research is taking longer than expected. Please try again.')
    }catch(err){updateAssistant(conversationId,assistantId,{status:undefined,text:err instanceof Error?err.message:'Something went wrong.'})}
    finally{setBusy(false)}
  }
  function handleSubmit(e:FormEvent){e.preventDefault();void submitResearch(draft)}
  function handleKeyDown(e:KeyboardEvent<HTMLTextAreaElement>){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();void submitResearch(draft)}}
  function newResearch(){if(busy)return;setActiveId(null);setError('');setDraft('');setMobileOpen(false);inputRef.current?.focus()}
  function selectConversation(id:string){if(busy)return;setActiveId(id);setMobileOpen(false);setError('')}
  async function signOut(){if(supabase)await supabase.auth.signOut();setSession(null);setMenuOpen(false)}
  function removeConversation(id:string){setConversations(prev=>prev.filter(c=>c.id!==id));if(activeId===id)setActiveId(null)}

  if(!authReady)return <div className="boot-screen"><div className="brand-mark"><BookOpen size={19}/></div><LoaderCircle className="spin" size={18}/></div>
  if(AUTH_REQUIRED&&supabase&&!session)return <AuthScreen supabase={supabase} onSuccess={()=>setAuthOpen(false)}/>

  return <div className="app-shell">
    {mobileOpen&&<button className="mobile-backdrop" onClick={()=>setMobileOpen(false)} aria-label="Close navigation"/>}
    <aside className={`sidebar ${mobileOpen?'sidebar-open':''}`}>
      <div className="sidebar-top"><a className="brand" href="#" onClick={e=>{e.preventDefault();newResearch()}}><span className="brand-mark"><BookOpen size={18}/></span><span>litera<span className="brand-period">.</span></span></a><button className="icon-btn mobile-close" onClick={()=>setMobileOpen(false)} aria-label="Close sidebar"><X size={18}/></button></div>
      <button className="new-chat" onClick={newResearch}><MessageSquarePlus size={17}/><span>New research</span><span className="new-chat-shortcut">⌘ K</span></button>
      <div className="sidebar-label">YOUR RESEARCH</div>
      <nav className="history-list" aria-label="Research history">{conversations.length?conversations.slice(0,30).map(c=><div className={`history-row ${activeId===c.id?'history-active':''}`} key={c.id}><button className="history-link" onClick={()=>selectConversation(c.id)}><span className="history-title">{c.title}</span></button><button className="history-more" aria-label={`Remove ${c.title}`} onClick={()=>removeConversation(c.id)}><X size={14}/></button></div>):<div className="history-empty"><Library size={17}/><span>Your past searches will appear here</span></div>}</nav>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={15}/><span>Your searches are saved in this browser.</span></div><button className="account-row" onClick={()=>AUTH_REQUIRED&&!session?setAuthOpen(true):setMenuOpen(v=>!v)}><span className="avatar">{session?.user.email?.[0]?.toUpperCase()||'R'}</span><span className="account-copy"><strong>{session?.user.email||'Researcher'}</strong><small>{session?'Signed in':'Personal workspace'}</small></span><MoreHorizontal size={17}/></button>{menuOpen&&<div className="account-menu">{session?<button onClick={signOut}><LogOut size={15}/> Sign out</button>:<button onClick={()=>{setAuthOpen(true);setMenuOpen(false)}}><LogIn size={15}/> Sign in</button>}</div>}</div>
    </aside>
    <main className="main-area">
      <header className="topbar"><div className="topbar-left"><button className="icon-btn mobile-menu" onClick={()=>setMobileOpen(true)} aria-label="Open navigation"><Menu size={19}/></button><span className="topbar-label">Research assistant</span></div><div className="topbar-actions"><span className="source-status"><span className="status-dot"/> Academic sources</span>{session?<button className="profile-button" onClick={()=>setMenuOpen(v=>!v)}><span className="avatar small">{session.user.email?.[0]?.toUpperCase()||'R'}</span><span>{session.user.email}</span><ChevronDown size={14}/></button>:AUTH_REQUIRED&&supabase?<button className="sign-in-button" onClick={()=>setAuthOpen(true)}><LogIn size={14}/> Sign in</button>:null}<a className="github-link" href="https://github.com/xRONITx/litera-research-assistant" target="_blank" rel="noreferrer">GitHub</a></div></header>
      <section className={`conversation ${active?'has-messages':''}`}>
        {!active?<div className="welcome-state"><div className="welcome-icon"><BookOpen size={22}/></div><h1>What are you researching?</h1><p>Ask a question. Get a literature review grounded in academic sources.</p><div className="suggestion-grid">{examples.map((item,i)=><button className="suggestion" key={item} onClick={()=>setDraft(item)}><span className="suggestion-index">0{i+1}</span><span>{item}</span><ArrowRight size={15}/></button>)}</div></div>:<div className="message-list" key={active.id}>{active.messages.map(message=><div className={`message message-${message.role}`} key={message.id}>{message.role==='user'?<><div className="user-avatar">{session?.user.email?.[0]?.toUpperCase()||'R'}</div><div className="user-message-text">{message.text}</div></>:<><div className="assistant-avatar"><BookOpen size={15}/></div><div className="assistant-content">{message.status?<div className="research-progress"><div className="progress-label"><LoaderCircle size={15} className="spin"/><span>{message.status}</span><b>{message.progress||0}%</b></div><div className="progress-bar"><span style={{width:`${message.progress||1}%`}}/></div><div className="progress-caption">Finding relevant papers and preparing your review</div></div>:message.report?<ReviewResult report={message.report} trace={message.trace||[]}/>:<div className="message-error"><CircleHelp size={16}/>{message.text}</div>}</div></>}</div>)}</div>}
      </section>
      <div className={`composer-dock ${active?'dock-followup':''}`}><form className="composer" onSubmit={handleSubmit}><textarea ref={inputRef} value={draft} onChange={e=>setDraft(e.target.value)} onKeyDown={handleKeyDown} placeholder="Ask any research question…" rows={1} aria-label="Research question" disabled={busy}/><div className="composer-bottom"><div className="composer-hint"><Search size={14}/><span>Searches academic papers and synthesizes the evidence</span></div><div className="composer-actions"><span className="enter-hint">↵ to send</span><button className="send-button" type="submit" disabled={!draft.trim()||busy} aria-label="Start research">{busy?<LoaderCircle size={17} className="spin"/>:<ArrowRight size={17}/>}</button></div></div></form><div className="composer-footnote">Litera can make mistakes. Check original papers before citing.</div>{error&&<div className="global-error">{error}</div>}</div>
    </main>
    {authOpen&&<AuthModal supabase={supabase} onClose={()=>setAuthOpen(false)} onSuccess={()=>setAuthOpen(false)}/>}
  </div>
}

function ReviewResult({report,trace}:{report:Report;trace:Array<{step:string;detail:string}>}){
  const [sourcesOpen,setSourcesOpen]=useState(false)
  const sourceList=report.sources||[]
  const review=(report.review||'').replace(/\[(\d+)\]/g,'[$1](litera-source:$1)')
  return <div className="review-result"><div className="result-eyebrow"><Sparkles size={14}/> LITERATURE REVIEW <span>·</span> {sourceList.length} SOURCES</div><ReactMarkdown components={{a:({href,children,...props})=>{const m=href?.match(/^litera-source:(\d+)$/);const source=m?sourceList[Number(m[1])-1]:undefined;return <a href={source?.url||href} target="_blank" rel="noreferrer" {...props}>{children}</a>}}}>{review}</ReactMarkdown><div className="evidence-callout"><ShieldCheck size={16}/><span>This is a first-pass synthesis. Verify the cited papers before using the findings in your work.</span></div><button className="sources-toggle" onClick={()=>setSourcesOpen(v=>!v)}><BookOpen size={15}/><strong>Sources</strong><span>{sourceList.length}</span>{sourcesOpen?<ChevronDown size={15}/>:<ArrowRight size={14}/>}</button>{sourcesOpen&&<div className="source-list">{sourceList.map((source,i)=><a className="source-item" key={`${source.id}-${i}`} href={source.url||source.pdf||'#'} target="_blank" rel="noreferrer"><span className="source-num">{i+1}</span><span className="source-detail"><strong>{source.title}</strong><small>{source.authors||'Authors not listed'}{source.venue?` · ${source.venue}`:''}{source.year?` · ${source.year}`:''}</small>{source.doi&&<small className="source-doi">DOI: {source.doi}</small>}</span><ArrowDown className="source-open" size={14}/></a>)}</div>}<div className="result-note"><Clock3 size={13}/> Completed {report.generated_at?new Date(report.generated_at*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):'just now'} <span>·</span> {trace.length} research steps</div></div>
}

function AuthScreen({supabase,onSuccess}:{supabase:AuthClient;onSuccess:()=>void}){
  const [mode,setMode]=useState<'signin'|'signup'>('signin')
  const [email,setEmail]=useState('');const [password,setPassword]=useState('');const [pending,setPending]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('')
  async function submit(e:FormEvent){e.preventDefault();if(!supabase)return;setPending(true);setError('');setNotice('');try{if(mode==='signup'){const {data,error:authError}=await supabase.auth.signUp({email,password});if(authError)throw authError;if(!data.session)setNotice('Check your email to confirm your account, then sign in.');else onSuccess()}else{const {error:authError}=await supabase.auth.signInWithPassword({email,password});if(authError)throw authError;onSuccess()}}catch(err){setError(err instanceof Error?err.message:'Unable to sign in. Try again.')}finally{setPending(false)}}
  return <div className="auth-page"><div className="auth-brand"><span className="brand-mark"><BookOpen size={18}/></span>litera<span className="brand-period">.</span></div><div className="auth-card"><div className="auth-symbol"><BookOpen size={20}/></div><h1>{mode==='signin'?'Welcome back':'Create your account'}</h1><p>Sign in to start a literature review.</p><form onSubmit={submit}><label>Email address<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoComplete="email"/></label><label>Password<input type="password" value={password} onChange={e=>setPassword(e.target.value)} required minLength={8} autoComplete={mode==='signin'?'current-password':'new-password'}/></label>{error&&<div className="auth-error">{error}</div>}{notice&&<div className="auth-notice">{notice}</div>}<button className="auth-submit" disabled={pending}>{pending?<LoaderCircle className="spin" size={16}/>:mode==='signin'?'Sign in':'Create account'}{!pending&&<ArrowRight size={15}/>}</button></form><div className="auth-switch">{mode==='signin'?"New to Litera?":"Already have an account?"}<button onClick={()=>{setMode(mode==='signin'?'signup':'signin');setError('');setNotice('')}}>{mode==='signin'?'Create an account':'Sign in'}</button></div></div><p className="auth-footer">Your research, organized and ready to revisit.</p></div>
}

function AuthModal({supabase,onClose,onSuccess}:{supabase:AuthClient|null;onClose:()=>void;onSuccess:()=>void}){
  const [mode,setMode]=useState<'signin'|'signup'>('signin');const [email,setEmail]=useState('');const [password,setPassword]=useState('');const [pending,setPending]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('')
  async function submit(e:FormEvent){e.preventDefault();if(!supabase)return;setPending(true);setError('');setNotice('');try{if(mode==='signup'){const {data,error:authError}=await supabase.auth.signUp({email,password});if(authError)throw authError;if(!data.session)setNotice('Check your email to confirm your account.');else onSuccess()}else{const {error:authError}=await supabase.auth.signInWithPassword({email,password});if(authError)throw authError;onSuccess()}}catch(err){setError(err instanceof Error?err.message:'Unable to sign in. Try again.')}finally{setPending(false)}}
  return <div className="modal-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-title"><button className="modal-close" onClick={onClose} aria-label="Close"><X size={18}/></button><div className="auth-symbol"><BookOpen size={19}/></div><h2 id="auth-title">{mode==='signin'?'Sign in to Litera':'Create your account'}</h2><p>Save your research and return to it anytime.</p><form onSubmit={submit}><label>Email address<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoComplete="email"/></label><label>Password<input type="password" value={password} onChange={e=>setPassword(e.target.value)} required minLength={8} autoComplete={mode==='signin'?'current-password':'new-password'}/></label>{error&&<div className="auth-error">{error}</div>}{notice&&<div className="auth-notice">{notice}</div>}<button className="auth-submit" disabled={pending}>{pending?<LoaderCircle className="spin" size={16}/>:mode==='signin'?'Sign in':'Create account'}{!pending&&<ArrowRight size={15}/>}</button></form><div className="auth-switch">{mode==='signin'?"New to Litera?":"Already have an account?"}<button onClick={()=>{setMode(mode==='signin'?'signup':'signin');setError('');setNotice('')}}>{mode==='signin'?'Create an account':'Sign in'}</button></div></section></div>
}
