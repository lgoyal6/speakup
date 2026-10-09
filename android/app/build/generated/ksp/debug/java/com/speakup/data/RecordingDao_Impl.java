package com.speakup.data;

import android.database.Cursor;
import android.os.CancellationSignal;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.room.CoroutinesRoom;
import androidx.room.EntityInsertionAdapter;
import androidx.room.RoomDatabase;
import androidx.room.RoomDatabaseKt;
import androidx.room.RoomSQLiteQuery;
import androidx.room.SharedSQLiteStatement;
import androidx.room.util.CursorUtil;
import androidx.room.util.DBUtil;
import androidx.sqlite.db.SupportSQLiteStatement;
import java.lang.Class;
import java.lang.Exception;
import java.lang.Integer;
import java.lang.Long;
import java.lang.Object;
import java.lang.Override;
import java.lang.String;
import java.lang.SuppressWarnings;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.Callable;
import javax.annotation.processing.Generated;
import kotlin.Unit;
import kotlin.coroutines.Continuation;
import kotlinx.coroutines.flow.Flow;

@Generated("androidx.room.RoomProcessor")
@SuppressWarnings({"unchecked", "deprecation"})
public final class RecordingDao_Impl extends RecordingDao {
  private final RoomDatabase __db;

  private final EntityInsertionAdapter<RecordingEntity> __insertionAdapterOfRecordingEntity;

  private final EntityInsertionAdapter<SyncClock> __insertionAdapterOfSyncClock;

  private final SharedSQLiteStatement __preparedStmtOfIncrementClock;

  private final SharedSQLiteStatement __preparedStmtOfState;

  private final SharedSQLiteStatement __preparedStmtOfEdit;

  private final SharedSQLiteStatement __preparedStmtOfDelete;

  private final SharedSQLiteStatement __preparedStmtOfDeleted;

  private final SharedSQLiteStatement __preparedStmtOfSynced;

  public RecordingDao_Impl(@NonNull final RoomDatabase __db) {
    this.__db = __db;
    this.__insertionAdapterOfRecordingEntity = new EntityInsertionAdapter<RecordingEntity>(__db) {
      @Override
      @NonNull
      protected String createQuery() {
        return "INSERT OR ABORT INTO `recordings` (`id`,`state`,`localPath`,`localOperationId`,`updatedAtEpochMs`,`deleted`,`rawTranscript`,`transcript`,`pendingEdit`,`serverRevision`,`error`) VALUES (?,?,?,?,?,?,?,?,?,?,?)";
      }

      @Override
      protected void bind(@NonNull final SupportSQLiteStatement statement,
          @NonNull final RecordingEntity entity) {
        statement.bindString(1, entity.getId());
        statement.bindString(2, entity.getState());
        statement.bindString(3, entity.getLocalPath());
        statement.bindLong(4, entity.getLocalOperationId());
        statement.bindLong(5, entity.getUpdatedAtEpochMs());
        final int _tmp = entity.getDeleted() ? 1 : 0;
        statement.bindLong(6, _tmp);
        if (entity.getRawTranscript() == null) {
          statement.bindNull(7);
        } else {
          statement.bindString(7, entity.getRawTranscript());
        }
        if (entity.getTranscript() == null) {
          statement.bindNull(8);
        } else {
          statement.bindString(8, entity.getTranscript());
        }
        if (entity.getPendingEdit() == null) {
          statement.bindNull(9);
        } else {
          statement.bindString(9, entity.getPendingEdit());
        }
        if (entity.getServerRevision() == null) {
          statement.bindNull(10);
        } else {
          statement.bindLong(10, entity.getServerRevision());
        }
        if (entity.getError() == null) {
          statement.bindNull(11);
        } else {
          statement.bindString(11, entity.getError());
        }
      }
    };
    this.__insertionAdapterOfSyncClock = new EntityInsertionAdapter<SyncClock>(__db) {
      @Override
      @NonNull
      protected String createQuery() {
        return "INSERT OR IGNORE INTO `sync_clock` (`id`,`operationId`) VALUES (?,?)";
      }

      @Override
      protected void bind(@NonNull final SupportSQLiteStatement statement,
          @NonNull final SyncClock entity) {
        statement.bindLong(1, entity.getId());
        statement.bindLong(2, entity.getOperationId());
      }
    };
    this.__preparedStmtOfIncrementClock = new SharedSQLiteStatement(__db) {
      @Override
      @NonNull
      public String createQuery() {
        final String _query = "UPDATE sync_clock SET operationId=operationId+1 WHERE id=1";
        return _query;
      }
    };
    this.__preparedStmtOfState = new SharedSQLiteStatement(__db) {
      @Override
      @NonNull
      public String createQuery() {
        final String _query = "UPDATE recordings SET state=?, error=?, updatedAtEpochMs=? WHERE id=? AND localOperationId=? AND deleted=0";
        return _query;
      }
    };
    this.__preparedStmtOfEdit = new SharedSQLiteStatement(__db) {
      @Override
      @NonNull
      public String createQuery() {
        final String _query = "UPDATE recordings SET transcript=?,pendingEdit=?,localOperationId=?,state='EDITED',error=NULL,updatedAtEpochMs=? WHERE id=? AND deleted=0";
        return _query;
      }
    };
    this.__preparedStmtOfDelete = new SharedSQLiteStatement(__db) {
      @Override
      @NonNull
      public String createQuery() {
        final String _query = "UPDATE recordings SET deleted=1,state='DELETE_PENDING',rawTranscript=NULL,transcript=NULL,pendingEdit=NULL,localOperationId=?,updatedAtEpochMs=? WHERE id=?";
        return _query;
      }
    };
    this.__preparedStmtOfDeleted = new SharedSQLiteStatement(__db) {
      @Override
      @NonNull
      public String createQuery() {
        final String _query = "UPDATE recordings SET state='DELETED',error=NULL WHERE id=? AND localOperationId=? AND deleted=1";
        return _query;
      }
    };
    this.__preparedStmtOfSynced = new SharedSQLiteStatement(__db) {
      @Override
      @NonNull
      public String createQuery() {
        final String _query = "UPDATE recordings SET state='TRANSCRIBED',rawTranscript=?,transcript=?,pendingEdit=NULL,serverRevision=?,error=NULL,updatedAtEpochMs=? WHERE id=? AND localOperationId=? AND deleted=0";
        return _query;
      }
    };
  }

  @Override
  public Object insert(final RecordingEntity recording,
      final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        __db.beginTransaction();
        try {
          __insertionAdapterOfRecordingEntity.insert(recording);
          __db.setTransactionSuccessful();
          return Unit.INSTANCE;
        } finally {
          __db.endTransaction();
        }
      }
    }, $completion);
  }

  @Override
  public Object seedClock(final SyncClock clock, final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        __db.beginTransaction();
        try {
          __insertionAdapterOfSyncClock.insert(clock);
          __db.setTransactionSuccessful();
          return Unit.INSTANCE;
        } finally {
          __db.endTransaction();
        }
      }
    }, $completion);
  }

  @Override
  public Object nextOperation(final Continuation<? super Long> $completion) {
    return RoomDatabaseKt.withTransaction(__db, (__cont) -> RecordingDao_Impl.super.nextOperation(__cont), $completion);
  }

  @Override
  public Object incrementClock(final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        final SupportSQLiteStatement _stmt = __preparedStmtOfIncrementClock.acquire();
        try {
          __db.beginTransaction();
          try {
            _stmt.executeUpdateDelete();
            __db.setTransactionSuccessful();
            return Unit.INSTANCE;
          } finally {
            __db.endTransaction();
          }
        } finally {
          __preparedStmtOfIncrementClock.release(_stmt);
        }
      }
    }, $completion);
  }

  @Override
  public Object state(final String id, final long operation, final String state, final String error,
      final long time, final Continuation<? super Integer> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Integer>() {
      @Override
      @NonNull
      public Integer call() throws Exception {
        final SupportSQLiteStatement _stmt = __preparedStmtOfState.acquire();
        int _argIndex = 1;
        _stmt.bindString(_argIndex, state);
        _argIndex = 2;
        if (error == null) {
          _stmt.bindNull(_argIndex);
        } else {
          _stmt.bindString(_argIndex, error);
        }
        _argIndex = 3;
        _stmt.bindLong(_argIndex, time);
        _argIndex = 4;
        _stmt.bindString(_argIndex, id);
        _argIndex = 5;
        _stmt.bindLong(_argIndex, operation);
        try {
          __db.beginTransaction();
          try {
            final Integer _result = _stmt.executeUpdateDelete();
            __db.setTransactionSuccessful();
            return _result;
          } finally {
            __db.endTransaction();
          }
        } finally {
          __preparedStmtOfState.release(_stmt);
        }
      }
    }, $completion);
  }

  @Override
  public Object edit(final String id, final String text, final long operation, final long time,
      final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        final SupportSQLiteStatement _stmt = __preparedStmtOfEdit.acquire();
        int _argIndex = 1;
        _stmt.bindString(_argIndex, text);
        _argIndex = 2;
        _stmt.bindString(_argIndex, text);
        _argIndex = 3;
        _stmt.bindLong(_argIndex, operation);
        _argIndex = 4;
        _stmt.bindLong(_argIndex, time);
        _argIndex = 5;
        _stmt.bindString(_argIndex, id);
        try {
          __db.beginTransaction();
          try {
            _stmt.executeUpdateDelete();
            __db.setTransactionSuccessful();
            return Unit.INSTANCE;
          } finally {
            __db.endTransaction();
          }
        } finally {
          __preparedStmtOfEdit.release(_stmt);
        }
      }
    }, $completion);
  }

  @Override
  public Object delete(final String id, final long operation, final long time,
      final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        final SupportSQLiteStatement _stmt = __preparedStmtOfDelete.acquire();
        int _argIndex = 1;
        _stmt.bindLong(_argIndex, operation);
        _argIndex = 2;
        _stmt.bindLong(_argIndex, time);
        _argIndex = 3;
        _stmt.bindString(_argIndex, id);
        try {
          __db.beginTransaction();
          try {
            _stmt.executeUpdateDelete();
            __db.setTransactionSuccessful();
            return Unit.INSTANCE;
          } finally {
            __db.endTransaction();
          }
        } finally {
          __preparedStmtOfDelete.release(_stmt);
        }
      }
    }, $completion);
  }

  @Override
  public Object deleted(final String id, final long operation,
      final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        final SupportSQLiteStatement _stmt = __preparedStmtOfDeleted.acquire();
        int _argIndex = 1;
        _stmt.bindString(_argIndex, id);
        _argIndex = 2;
        _stmt.bindLong(_argIndex, operation);
        try {
          __db.beginTransaction();
          try {
            _stmt.executeUpdateDelete();
            __db.setTransactionSuccessful();
            return Unit.INSTANCE;
          } finally {
            __db.endTransaction();
          }
        } finally {
          __preparedStmtOfDeleted.release(_stmt);
        }
      }
    }, $completion);
  }

  @Override
  public Object synced(final String id, final long operation, final String raw, final String text,
      final int revision, final long time, final Continuation<? super Integer> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Integer>() {
      @Override
      @NonNull
      public Integer call() throws Exception {
        final SupportSQLiteStatement _stmt = __preparedStmtOfSynced.acquire();
        int _argIndex = 1;
        _stmt.bindString(_argIndex, raw);
        _argIndex = 2;
        _stmt.bindString(_argIndex, text);
        _argIndex = 3;
        _stmt.bindLong(_argIndex, revision);
        _argIndex = 4;
        _stmt.bindLong(_argIndex, time);
        _argIndex = 5;
        _stmt.bindString(_argIndex, id);
        _argIndex = 6;
        _stmt.bindLong(_argIndex, operation);
        try {
          __db.beginTransaction();
          try {
            final Integer _result = _stmt.executeUpdateDelete();
            __db.setTransactionSuccessful();
            return _result;
          } finally {
            __db.endTransaction();
          }
        } finally {
          __preparedStmtOfSynced.release(_stmt);
        }
      }
    }, $completion);
  }

  @Override
  public Flow<List<RecordingEntity>> history() {
    final String _sql = "SELECT * FROM recordings WHERE deleted=0 ORDER BY updatedAtEpochMs DESC";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 0);
    return CoroutinesRoom.createFlow(__db, false, new String[] {"recordings"}, new Callable<List<RecordingEntity>>() {
      @Override
      @NonNull
      public List<RecordingEntity> call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final int _cursorIndexOfId = CursorUtil.getColumnIndexOrThrow(_cursor, "id");
          final int _cursorIndexOfState = CursorUtil.getColumnIndexOrThrow(_cursor, "state");
          final int _cursorIndexOfLocalPath = CursorUtil.getColumnIndexOrThrow(_cursor, "localPath");
          final int _cursorIndexOfLocalOperationId = CursorUtil.getColumnIndexOrThrow(_cursor, "localOperationId");
          final int _cursorIndexOfUpdatedAtEpochMs = CursorUtil.getColumnIndexOrThrow(_cursor, "updatedAtEpochMs");
          final int _cursorIndexOfDeleted = CursorUtil.getColumnIndexOrThrow(_cursor, "deleted");
          final int _cursorIndexOfRawTranscript = CursorUtil.getColumnIndexOrThrow(_cursor, "rawTranscript");
          final int _cursorIndexOfTranscript = CursorUtil.getColumnIndexOrThrow(_cursor, "transcript");
          final int _cursorIndexOfPendingEdit = CursorUtil.getColumnIndexOrThrow(_cursor, "pendingEdit");
          final int _cursorIndexOfServerRevision = CursorUtil.getColumnIndexOrThrow(_cursor, "serverRevision");
          final int _cursorIndexOfError = CursorUtil.getColumnIndexOrThrow(_cursor, "error");
          final List<RecordingEntity> _result = new ArrayList<RecordingEntity>(_cursor.getCount());
          while (_cursor.moveToNext()) {
            final RecordingEntity _item;
            final String _tmpId;
            _tmpId = _cursor.getString(_cursorIndexOfId);
            final String _tmpState;
            _tmpState = _cursor.getString(_cursorIndexOfState);
            final String _tmpLocalPath;
            _tmpLocalPath = _cursor.getString(_cursorIndexOfLocalPath);
            final long _tmpLocalOperationId;
            _tmpLocalOperationId = _cursor.getLong(_cursorIndexOfLocalOperationId);
            final long _tmpUpdatedAtEpochMs;
            _tmpUpdatedAtEpochMs = _cursor.getLong(_cursorIndexOfUpdatedAtEpochMs);
            final boolean _tmpDeleted;
            final int _tmp;
            _tmp = _cursor.getInt(_cursorIndexOfDeleted);
            _tmpDeleted = _tmp != 0;
            final String _tmpRawTranscript;
            if (_cursor.isNull(_cursorIndexOfRawTranscript)) {
              _tmpRawTranscript = null;
            } else {
              _tmpRawTranscript = _cursor.getString(_cursorIndexOfRawTranscript);
            }
            final String _tmpTranscript;
            if (_cursor.isNull(_cursorIndexOfTranscript)) {
              _tmpTranscript = null;
            } else {
              _tmpTranscript = _cursor.getString(_cursorIndexOfTranscript);
            }
            final String _tmpPendingEdit;
            if (_cursor.isNull(_cursorIndexOfPendingEdit)) {
              _tmpPendingEdit = null;
            } else {
              _tmpPendingEdit = _cursor.getString(_cursorIndexOfPendingEdit);
            }
            final Integer _tmpServerRevision;
            if (_cursor.isNull(_cursorIndexOfServerRevision)) {
              _tmpServerRevision = null;
            } else {
              _tmpServerRevision = _cursor.getInt(_cursorIndexOfServerRevision);
            }
            final String _tmpError;
            if (_cursor.isNull(_cursorIndexOfError)) {
              _tmpError = null;
            } else {
              _tmpError = _cursor.getString(_cursorIndexOfError);
            }
            _item = new RecordingEntity(_tmpId,_tmpState,_tmpLocalPath,_tmpLocalOperationId,_tmpUpdatedAtEpochMs,_tmpDeleted,_tmpRawTranscript,_tmpTranscript,_tmpPendingEdit,_tmpServerRevision,_tmpError);
            _result.add(_item);
          }
          return _result;
        } finally {
          _cursor.close();
        }
      }

      @Override
      protected void finalize() {
        _statement.release();
      }
    });
  }

  @Override
  public Object find(final String id, final Continuation<? super RecordingEntity> $completion) {
    final String _sql = "SELECT * FROM recordings WHERE id=?";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 1);
    int _argIndex = 1;
    _statement.bindString(_argIndex, id);
    final CancellationSignal _cancellationSignal = DBUtil.createCancellationSignal();
    return CoroutinesRoom.execute(__db, false, _cancellationSignal, new Callable<RecordingEntity>() {
      @Override
      @Nullable
      public RecordingEntity call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final int _cursorIndexOfId = CursorUtil.getColumnIndexOrThrow(_cursor, "id");
          final int _cursorIndexOfState = CursorUtil.getColumnIndexOrThrow(_cursor, "state");
          final int _cursorIndexOfLocalPath = CursorUtil.getColumnIndexOrThrow(_cursor, "localPath");
          final int _cursorIndexOfLocalOperationId = CursorUtil.getColumnIndexOrThrow(_cursor, "localOperationId");
          final int _cursorIndexOfUpdatedAtEpochMs = CursorUtil.getColumnIndexOrThrow(_cursor, "updatedAtEpochMs");
          final int _cursorIndexOfDeleted = CursorUtil.getColumnIndexOrThrow(_cursor, "deleted");
          final int _cursorIndexOfRawTranscript = CursorUtil.getColumnIndexOrThrow(_cursor, "rawTranscript");
          final int _cursorIndexOfTranscript = CursorUtil.getColumnIndexOrThrow(_cursor, "transcript");
          final int _cursorIndexOfPendingEdit = CursorUtil.getColumnIndexOrThrow(_cursor, "pendingEdit");
          final int _cursorIndexOfServerRevision = CursorUtil.getColumnIndexOrThrow(_cursor, "serverRevision");
          final int _cursorIndexOfError = CursorUtil.getColumnIndexOrThrow(_cursor, "error");
          final RecordingEntity _result;
          if (_cursor.moveToFirst()) {
            final String _tmpId;
            _tmpId = _cursor.getString(_cursorIndexOfId);
            final String _tmpState;
            _tmpState = _cursor.getString(_cursorIndexOfState);
            final String _tmpLocalPath;
            _tmpLocalPath = _cursor.getString(_cursorIndexOfLocalPath);
            final long _tmpLocalOperationId;
            _tmpLocalOperationId = _cursor.getLong(_cursorIndexOfLocalOperationId);
            final long _tmpUpdatedAtEpochMs;
            _tmpUpdatedAtEpochMs = _cursor.getLong(_cursorIndexOfUpdatedAtEpochMs);
            final boolean _tmpDeleted;
            final int _tmp;
            _tmp = _cursor.getInt(_cursorIndexOfDeleted);
            _tmpDeleted = _tmp != 0;
            final String _tmpRawTranscript;
            if (_cursor.isNull(_cursorIndexOfRawTranscript)) {
              _tmpRawTranscript = null;
            } else {
              _tmpRawTranscript = _cursor.getString(_cursorIndexOfRawTranscript);
            }
            final String _tmpTranscript;
            if (_cursor.isNull(_cursorIndexOfTranscript)) {
              _tmpTranscript = null;
            } else {
              _tmpTranscript = _cursor.getString(_cursorIndexOfTranscript);
            }
            final String _tmpPendingEdit;
            if (_cursor.isNull(_cursorIndexOfPendingEdit)) {
              _tmpPendingEdit = null;
            } else {
              _tmpPendingEdit = _cursor.getString(_cursorIndexOfPendingEdit);
            }
            final Integer _tmpServerRevision;
            if (_cursor.isNull(_cursorIndexOfServerRevision)) {
              _tmpServerRevision = null;
            } else {
              _tmpServerRevision = _cursor.getInt(_cursorIndexOfServerRevision);
            }
            final String _tmpError;
            if (_cursor.isNull(_cursorIndexOfError)) {
              _tmpError = null;
            } else {
              _tmpError = _cursor.getString(_cursorIndexOfError);
            }
            _result = new RecordingEntity(_tmpId,_tmpState,_tmpLocalPath,_tmpLocalOperationId,_tmpUpdatedAtEpochMs,_tmpDeleted,_tmpRawTranscript,_tmpTranscript,_tmpPendingEdit,_tmpServerRevision,_tmpError);
          } else {
            _result = null;
          }
          return _result;
        } finally {
          _cursor.close();
          _statement.release();
        }
      }
    }, $completion);
  }

  @Override
  public Object interrupted(final Continuation<? super List<RecordingEntity>> $completion) {
    final String _sql = "SELECT * FROM recordings WHERE state='LOCAL_DRAFT' AND deleted=0";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 0);
    final CancellationSignal _cancellationSignal = DBUtil.createCancellationSignal();
    return CoroutinesRoom.execute(__db, false, _cancellationSignal, new Callable<List<RecordingEntity>>() {
      @Override
      @NonNull
      public List<RecordingEntity> call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final int _cursorIndexOfId = CursorUtil.getColumnIndexOrThrow(_cursor, "id");
          final int _cursorIndexOfState = CursorUtil.getColumnIndexOrThrow(_cursor, "state");
          final int _cursorIndexOfLocalPath = CursorUtil.getColumnIndexOrThrow(_cursor, "localPath");
          final int _cursorIndexOfLocalOperationId = CursorUtil.getColumnIndexOrThrow(_cursor, "localOperationId");
          final int _cursorIndexOfUpdatedAtEpochMs = CursorUtil.getColumnIndexOrThrow(_cursor, "updatedAtEpochMs");
          final int _cursorIndexOfDeleted = CursorUtil.getColumnIndexOrThrow(_cursor, "deleted");
          final int _cursorIndexOfRawTranscript = CursorUtil.getColumnIndexOrThrow(_cursor, "rawTranscript");
          final int _cursorIndexOfTranscript = CursorUtil.getColumnIndexOrThrow(_cursor, "transcript");
          final int _cursorIndexOfPendingEdit = CursorUtil.getColumnIndexOrThrow(_cursor, "pendingEdit");
          final int _cursorIndexOfServerRevision = CursorUtil.getColumnIndexOrThrow(_cursor, "serverRevision");
          final int _cursorIndexOfError = CursorUtil.getColumnIndexOrThrow(_cursor, "error");
          final List<RecordingEntity> _result = new ArrayList<RecordingEntity>(_cursor.getCount());
          while (_cursor.moveToNext()) {
            final RecordingEntity _item;
            final String _tmpId;
            _tmpId = _cursor.getString(_cursorIndexOfId);
            final String _tmpState;
            _tmpState = _cursor.getString(_cursorIndexOfState);
            final String _tmpLocalPath;
            _tmpLocalPath = _cursor.getString(_cursorIndexOfLocalPath);
            final long _tmpLocalOperationId;
            _tmpLocalOperationId = _cursor.getLong(_cursorIndexOfLocalOperationId);
            final long _tmpUpdatedAtEpochMs;
            _tmpUpdatedAtEpochMs = _cursor.getLong(_cursorIndexOfUpdatedAtEpochMs);
            final boolean _tmpDeleted;
            final int _tmp;
            _tmp = _cursor.getInt(_cursorIndexOfDeleted);
            _tmpDeleted = _tmp != 0;
            final String _tmpRawTranscript;
            if (_cursor.isNull(_cursorIndexOfRawTranscript)) {
              _tmpRawTranscript = null;
            } else {
              _tmpRawTranscript = _cursor.getString(_cursorIndexOfRawTranscript);
            }
            final String _tmpTranscript;
            if (_cursor.isNull(_cursorIndexOfTranscript)) {
              _tmpTranscript = null;
            } else {
              _tmpTranscript = _cursor.getString(_cursorIndexOfTranscript);
            }
            final String _tmpPendingEdit;
            if (_cursor.isNull(_cursorIndexOfPendingEdit)) {
              _tmpPendingEdit = null;
            } else {
              _tmpPendingEdit = _cursor.getString(_cursorIndexOfPendingEdit);
            }
            final Integer _tmpServerRevision;
            if (_cursor.isNull(_cursorIndexOfServerRevision)) {
              _tmpServerRevision = null;
            } else {
              _tmpServerRevision = _cursor.getInt(_cursorIndexOfServerRevision);
            }
            final String _tmpError;
            if (_cursor.isNull(_cursorIndexOfError)) {
              _tmpError = null;
            } else {
              _tmpError = _cursor.getString(_cursorIndexOfError);
            }
            _item = new RecordingEntity(_tmpId,_tmpState,_tmpLocalPath,_tmpLocalOperationId,_tmpUpdatedAtEpochMs,_tmpDeleted,_tmpRawTranscript,_tmpTranscript,_tmpPendingEdit,_tmpServerRevision,_tmpError);
            _result.add(_item);
          }
          return _result;
        } finally {
          _cursor.close();
          _statement.release();
        }
      }
    }, $completion);
  }

  @Override
  public Object clock(final Continuation<? super Long> $completion) {
    final String _sql = "SELECT operationId FROM sync_clock WHERE id=1";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 0);
    final CancellationSignal _cancellationSignal = DBUtil.createCancellationSignal();
    return CoroutinesRoom.execute(__db, false, _cancellationSignal, new Callable<Long>() {
      @Override
      @NonNull
      public Long call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final long _result;
          if (_cursor.moveToFirst()) {
            _result = _cursor.getLong(0);
          } else {
            _result = 0L;
          }
          return _result;
        } finally {
          _cursor.close();
          _statement.release();
        }
      }
    }, $completion);
  }

  @Override
  public Object resumableIds(final Continuation<? super List<String>> $completion) {
    final String _sql = "SELECT id FROM recordings WHERE state NOT IN ('LOCAL_DRAFT','INTERRUPTED','DELETED')";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 0);
    final CancellationSignal _cancellationSignal = DBUtil.createCancellationSignal();
    return CoroutinesRoom.execute(__db, false, _cancellationSignal, new Callable<List<String>>() {
      @Override
      @NonNull
      public List<String> call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final List<String> _result = new ArrayList<String>(_cursor.getCount());
          while (_cursor.moveToNext()) {
            final String _item;
            _item = _cursor.getString(0);
            _result.add(_item);
          }
          return _result;
        } finally {
          _cursor.close();
          _statement.release();
        }
      }
    }, $completion);
  }

  @NonNull
  public static List<Class<?>> getRequiredConverters() {
    return Collections.emptyList();
  }
}
