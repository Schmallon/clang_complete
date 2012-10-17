"
" File: clang_complete.vim
" Author: Xavier Deguillard <deguilx@gmail.com>
"
" Description: Use of clang to complete in C/C++.
"
" Help: Use :help clang_complete
"

autocmd FileType c,cpp,objc,objcpp call <SID>ClangCompleteInit()

let b:clang_parameters = ''
let b:clang_user_options = ''
let b:my_changedtick = 0
let b:update_succeeded = 0
let s:python_for_clang_loaded = 0

" Store plugin path, as this is available only when sourcing the file,
" not during a function call.
let s:plugin_path = escape(expand('<sfile>:p:h'), '\')

function! s:ClangCompleteInit()
  if !exists('g:clang_auto_select')
    let g:clang_auto_select = 0
  endif

  if !exists('g:clang_complete_auto')
    let g:clang_complete_auto = 1
  endif

  if !exists('g:clang_close_preview')
    let g:clang_close_preview = 0
  endif

  if !exists('g:clang_complete_copen')
    let g:clang_complete_copen = 0
  endif

  if !exists('g:clang_hl_errors')
    let g:clang_hl_errors = 1
  endif

  if !exists('g:clang_periodic_quickfix')
    let g:clang_periodic_quickfix = 0
  endif

  if !exists('g:clang_snippets')
    let g:clang_snippets = 0
  endif

  if !exists('g:clang_snippets_engine')
    let g:clang_snippets_engine = 'clang_complete'
  endif

  if !exists('g:clang_exec')
    let g:clang_exec = 'clang'
  endif

  if !exists('g:clang_user_options')
    let g:clang_user_options = ''
  endif

  if !exists('g:clang_complete_macros')
    let g:clang_complete_macros = 0
  endif

  if !exists('g:clang_complete_patterns')
    let g:clang_complete_patterns = 0
  endif

  if !exists('g:clang_debug')
    let g:clang_debug = 0
  endif

  if !exists('g:clang_sort_algo')
    let g:clang_sort_algo = 'priority'
  endif

  if !exists('g:clang_auto_user_options')
    let g:clang_auto_user_options = 'path, .clang_complete, clang'
  endif

  call LoadUserOptions()

  inoremap <expr> <buffer> <C-X><C-U> <SID>LaunchCompletion()
  inoremap <expr> <buffer> . <SID>CompleteDot()
  inoremap <expr> <buffer> > <SID>CompleteArrow()
  inoremap <expr> <buffer> : <SID>CompleteColon()
  inoremap <expr> <buffer> <CR> <SID>HandlePossibleSelectionEnter()

  if g:clang_snippets == 1
    call g:ClangSetSnippetEngine(g:clang_snippets_engine)
  endif

  " Force menuone. Without it, when there's only one completion result,
  " it can be confusing (not completing and no popup)
  if g:clang_auto_select != 2
    set completeopt-=menu
    set completeopt+=menuone
  endif

  " Disable every autocmd that could have been set.
  "augroup ClangComplete
    "autocmd!
  "augroup end

  let b:should_overload = 0
  let b:my_changedtick = b:changedtick
  let b:update_succeeded = 0
  let b:clang_parameters = '-x c'

  if &filetype == 'objc'
    let b:clang_parameters = '-x objective-c'
  endif

  if &filetype == 'cpp' || &filetype == 'objcpp'
    let b:clang_parameters .= '++'
  endif

  if expand('%:e') =~ 'h*'
    let b:clang_parameters .= '-header'
  endif

  let g:clang_complete_lib_flags = 0

  if g:clang_complete_macros == 1
    let b:clang_parameters .= ' -code-completion-macros'
    let g:clang_complete_lib_flags = 1
  endif

  if g:clang_complete_patterns == 1
    let b:clang_parameters .= ' -code-completion-patterns'
    let g:clang_complete_lib_flags += 2
  endif

  setlocal completefunc=ClangComplete
  setlocal omnifunc=ClangComplete

  if g:clang_periodic_quickfix == 1
    augroup ClangComplete
      autocmd CursorHold,CursorHoldI <buffer> call <SID>DoPeriodicQuickFix()
    augroup end
  endif

  " Load the python bindings of libclang
  if has('python')

    if s:python_for_clang_loaded == 0
      call s:initClangCompletePython()
      let s:python_for_clang_loaded = 1
    endif
  else
    echoe 'clang_complete: No python support available.'
    return
  endif

  "Ensure we have a location list
  call setloclist(0, [])

endfunction

function! LoadUserOptions()
  let b:clang_user_options = ''

  let l:option_sources = split(g:clang_auto_user_options, ',')
  let l:remove_spaces_cmd = 'substitute(v:val, "\\s*\\(.*\\)\\s*", "\\1", "")'
  let l:option_sources = map(l:option_sources, l:remove_spaces_cmd)

  for l:source in l:option_sources
    if l:source == 'path'
      call s:parsePathOption()
    elseif l:source == '.clang_complete'
      call s:parseConfig()
    else
      let l:getopts = 'getopts#' . l:source . '#getopts'
      silent call eval(l:getopts . '()')
    endif
  endfor
endfunction

function! s:parseConfig()
  let l:local_conf = findfile('.clang_complete', getcwd() . ',.;')
  if l:local_conf == '' || !filereadable(l:local_conf)
    return
  endif

  let l:root = substitute(fnamemodify(l:local_conf, ':p:h'), '\', '/', 'g')

  let l:opts = readfile(l:local_conf)
  for l:opt in l:opts
    " Use forward slashes only
    let l:opt = substitute(l:opt, '\', '/', 'g')
    " Handling of absolute path
    if matchstr(l:opt, '\C-I\s*/') != ''
      let l:opt = substitute(l:opt, '\C-I\s*\(/\%(\w\|\\\s\)*\)',
            \ '-I' . '\1', 'g')
    elseif s:isWindows() && matchstr(l:opt, '\C-I\s*[a-zA-Z]:/') != ''
      let l:opt = substitute(l:opt, '\C-I\s*\([a-zA-Z:]/\%(\w\|\\\s\)*\)',
            \ '-I' . '\1', 'g')
    else
      let l:opt = substitute(l:opt, '\C-I\s*\(\%(\w\|\.\|/\|\\\s\)*\)',
            \ '-I' . l:root . '/\1', 'g')
    endif
    let b:clang_user_options .= ' ' . l:opt
  endfor
endfunction

function! s:parsePathOption()
  let l:dirs = split(&path, ',')
  for l:dir in l:dirs
    if len(l:dir) == 0 || !isdirectory(l:dir)
      continue
    endif

    " Add only absolute paths
    if matchstr(l:dir, '\s*/') != ''
      let l:opt = '-I' . l:dir
      let b:clang_user_options .= ' ' . l:opt
    endif
  endfor
endfunction

function! s:initClangCompletePython()
  " Only parse the python library once
  if !exists('s:libclang_loaded')
    python import sys
    python import vim
    if exists('g:clang_library_path')
      " Load the library from the given library path.
      exe 'python sys.argv = ["' . escape(g:clang_library_path, '\') . '"]'
    else
      " By setting argv[0] to '' force the python bindings to load the library
      " from the normal system search path.
      python sys.argv[0] = ''
    endif

    exe 'python sys.path = ["' . s:plugin_path . '"] + sys.path'
    exe 'pyfile ' . s:plugin_path . '/libclang.py'
    python vim_interface = VimInterface()
    python clang_plugin = ClangPlugin(vim_interface, vim.eval('g:clang_complete_lib_flags'))

    augroup ClangComplete
      "Does not really detect all changes, e.g. yanks. Any better ideas?
      autocmd FileChangedShellPost *.cpp,*.c,*.h python clang_plugin.file_changed()
      autocmd BufWritePost *.cpp,*.c,*.h python clang_plugin.file_changed()
      autocmd InsertLeave *.cpp,*.c,*.h python clang_plugin.file_changed()
      "autocmd InsertCharPre * python clang_plugin.file_changed()
      autocmd BufReadPost *.cpp,*.c,*.h python clang_plugin.file_opened()
      autocmd VimLeave * python clang_plugin.terminate()
    augroup end
  let s:libclang_loaded = 1
  endif
endfunction

function! s:NoopKeypress()
  if mode() == "n"
    call feedkeys("f\e", "n")
  elseif mode() == "i"
    call feedkeys("\<C-R>\e", "n")
  endif
endfunction

function! g:TogglePeriodicQuickfix()
  let g:clang_periodic_quickfix = !g:clang_periodic_quickfix
  if g:clang_periodic_quickfix
    echo "Auto-Quickfix on"
  else
    echo "Auto-Quickfix off"
  endif

endfunction

function! s:DoPeriodicQuickFix()
  if !g:clang_periodic_quickfix
    return
  endif

  if b:my_changedtick != b:changedtick
    python clang_plugin.file_changed()
    let b:my_changedtick = b:changedtick
    let b:update_succeeded = 0
  endif

  if !b:update_succeeded
    python vim.command('let b:update_succeeded = ' + str(clang_plugin.try_update_diagnostics()))
  endif

  if !b:update_succeeded
    "Results in another update
    call s:NoopKeypress()
  endif
endfunction

function! g:CalledFromPythonClangDisplayQuickFix(quick_fix)
  " Clear the bad spell, the user may have corrected them.
  syntax clear SpellBad
  syntax clear SpellLocal

  if g:clang_complete_copen == 1
    " We should get back to the original buffer
    let l:bufnr = bufnr('%')

    " Workaround:
    " http://vim.1045645.n5.nabble.com/setqflist-inconsistency-td1211423.html
    if a:quick_fix == []
      echo "No Errors"
      lclose
    else
      lopen
      setlocal nospell
    endif

    let l:winbufnr = bufwinnr(l:bufnr)
    exe l:winbufnr . 'wincmd w'
  endif
  call setloclist(0, a:quick_fix)
endfunction

let b:col = 0

function! ClangComplete(findstart, base)
  if a:findstart
    let l:line = getline('.')
    let l:start = col('.') - 1
    let b:clang_complete_type = 1
    let l:wsstart = l:start
    if l:line[l:wsstart - 1] =~ '\s'
      while l:wsstart > 0 && l:line[l:wsstart - 1] =~ '\s'
        let l:wsstart -= 1
      endwhile
    endif
    if l:line[l:wsstart - 1] =~ '[(,]'
      let b:should_overload = 1
      let b:col = l:wsstart + 1
      return l:wsstart
    endif
    let b:should_overload = 0
    while l:start > 0 && l:line[l:start - 1] =~ '\i'
      let l:start -= 1
    endwhile
    if l:line[l:start - 2:] =~ '->' || l:line[l:start - 1] == '.'
      let b:clang_complete_type = 0
    endif
    let b:col = l:start + 1
    return l:start
  else
    if g:clang_debug == 1
      let l:time_start = reltime()
    endif

    if g:clang_snippets == 1
      call b:ResetSnip()
    endif

    python vim.command('let l:res = ' + str(clang_plugin.get_current_completions(vim.eval('a:base'))))

    for item in l:res
      if g:clang_snippets == 1
        let item['word'] = b:AddSnip(item['info'], item['args_pos'])
      else
        let item['word'] = item['abbr']
      endif
    endfor

    inoremap <expr> <buffer> <C-Y> <SID>HandlePossibleSelectionCtrlY()
    augroup ClangComplete
      au CursorMovedI <buffer> call <SID>TriggerSnippet()
    augroup end
    let b:snippet_chosen = 0

  if g:clang_debug == 1
    echom 'clang_complete: completion time '. split(reltimestr(reltime(l:time_start)))[0]
  endif
  return l:res
endif
endfunction

function! s:HandlePossibleSelectionEnter()
  if pumvisible()
    let b:snippet_chosen = 1
    return "\<C-Y>"
  end
  return "\<CR>"
endfunction

function! s:HandlePossibleSelectionCtrlY()
  if pumvisible()
    let b:snippet_chosen = 1
  end
  return "\<C-Y>"
endfunction

function! s:TriggerSnippet()
  " Dont bother doing anything until we're sure the user exited the menu
  if !b:snippet_chosen
    return
  endif

  if g:clang_snippets == 1
    " Stop monitoring as we'll trigger a snippet
    silent! iunmap <buffer> <C-Y>
    augroup ClangComplete
      au! CursorMovedI <buffer>
    augroup end

    " Trigger the snippet
    call b:TriggerSnip()
  endif

  if g:clang_close_preview
    pclose
  endif
endfunction

function! s:ShouldComplete()
  if (getline('.') =~ '#\s*\(include\|import\)')
    return 0
  else
    if col('.') == 1
      return 1
    endif
    for l:id in synstack(line('.'), col('.') - 1)
      if match(synIDattr(l:id, 'name'), '\CComment\|String\|Number')
            \ != -1
        return 0
      endif
    endfor
    return 1
  endif
endfunction

function! s:LaunchCompletion()
  let l:result = ""
  if s:ShouldComplete()
    let l:result = "\<C-X>\<C-U>"
    if g:clang_auto_select != 2
      let l:result .= "\<C-P>"
    endif
    if g:clang_auto_select == 1
      let l:result .= "\<C-R>=(pumvisible() ? \"\\<Down>\" : '')\<CR>"
    endif
  endif
  return l:result
endfunction

function! s:CompleteDot()
  if g:clang_complete_auto == 1
    return '.' . s:LaunchCompletion()
  endif
  return '.'
endfunction

function! s:CompleteArrow()
  if g:clang_complete_auto != 1 || getline('.')[col('.') - 2] != '-'
    return '>'
  endif
  return '>' . s:LaunchCompletion()
endfunction

function! s:CompleteColon()
  if g:clang_complete_auto != 1 || getline('.')[col('.') - 2] != ':'
    return ':'
  endif
  return ':' . s:LaunchCompletion()
endfunction

function! g:ClangSetSnippetEngine(engine_name)
  try
    call eval('snippets#' . a:engine_name . '#init()')
    let b:AddSnip = function('snippets#' . a:engine_name . '#add_snippet')
    let b:ResetSnip = function('snippets#' . a:engine_name . '#reset')
    let b:TriggerSnip = function('snippets#' . a:engine_name . '#trigger')
  catch /^Vim\%((\a\+)\)\=:E117/
    echoe 'Snippets engine ' . a:engine_name . ' not found.'
    let g:clang_snippets = 0
  endtry
endfunction
" vim: set ts=2 sts=2 sw=2 expandtab :
