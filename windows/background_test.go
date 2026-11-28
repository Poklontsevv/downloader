package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestPausedDownloadRemainsAvailableWhileWindowIsHidden(t *testing.T) {
	for _, keepPaused := range []bool{true, false} {
		ctx, cancel := context.WithCancel(context.Background())
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			io.WriteString(w, `{"jobs":[{"state":"paused"}]}`)
		}))
		active := make(chan struct{})
		done := make(chan struct{})
		go func() {
			defer close(done)
			waitForBackgroundTasks(ctx, server.URL+"/", nil, keepPaused, func() { close(active) })
		}()
		select {
		case <-active:
			if !keepPaused {
				t.Error("a paused download blocked the update")
			}
		case <-done:
			if keepPaused {
				t.Error("the background process exited with a paused download")
			}
		case <-time.After(time.Second):
			t.Error("expected an activity check")
		}
		cancel()
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Error("the background check continued after the window was restored")
		}
		server.Close()
	}
}
